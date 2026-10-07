"""Contract tests for the ingestion pipeline (docs/CONTRACT.md, G1-G6).

Every test builds its own state with the `replay` fixture, so tests are
independent and can run in any order. None of them asserts a hard-coded row
count: expected values are derived either from the raw NDJSON on disk
(independent of the pipeline) or from a second run of the pipeline itself
(differential testing). They stay meaningful when the data changes.

Run:  make test        (test_rollup_api_* also needs `make api` running)
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
import pytest


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def snapshot(db):
    """Business content of the three contract tables, minus surrogate keys and
    wall-clock columns (sightings.id, findings.id, processed_at)."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT batch_id, source, asset_key, rule_key, severity, detected_at "
            "FROM sightings ORDER BY 1, 2, 3, 4"
        )
        sightings = cur.fetchall()
        cur.execute(
            "SELECT source, asset_key, rule_key, severity, cvss, first_seen_at, last_seen_at "
            "FROM findings ORDER BY 1, 2, 3"
        )
        findings = cur.fetchall()
        cur.execute("SELECT day, severity, n FROM daily_severity_rollup ORDER BY 1, 2")
        rollup = cur.fetchall()
    return {"sightings": sightings, "findings": findings, "rollup": rollup}


def describe_diff(a, b):
    """Human-readable summary of how two snapshots differ (for assert messages)."""
    out = []
    for table in ("sightings", "findings", "rollup"):
        sa, sb = set(a[table]), set(b[table])
        if sa != sb:
            only_a, only_b = sorted(sa - sb, key=str), sorted(sb - sa, key=str)
            out.append(f"{table}: {len(only_a)} rows only in first, {len(only_b)} only in second; "
                       f"e.g. {only_a[:2]} vs {only_b[:2]}")
    return "\n".join(out) or "identical"


def _utc(ts: str) -> datetime:
    # Python 3.10's fromisoformat does not accept a trailing 'Z'.
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def raw_identity_and_event(rec: dict):
    """Independent reading of the three payload shapes in docs/CONTRACT.md.
    Deliberately NOT importing pipeline.normalize: this is the oracle.
    Returns (source, asset_key, rule_key, severity, detected_at_utc)."""
    src = rec["source"]
    if src == "scanner_a":
        host, rule, sev, ts = rec["host"], rec["check_id"], rec["sev"], rec["ts"]
    elif src == "scanner_b":
        host, rule, sev, ts = (rec["asset"]["hostname"], rec["vuln"]["id"],
                               rec["vuln"]["severity"], rec["observed_at"])
    elif src == "scanner_c":
        host, rule, sev, ts = (rec["target"], rec["finding"]["key"],
                               rec["level"], rec["first_observed"])
    else:
        raise AssertionError(f"unknown source in raw data: {src!r}")
    return src, host, rule, str(sev).strip().lower(), _utc(ts)


def expected_rollup(raw_records, batch_ids):
    """Sightings per UTC calendar day per severity, computed straight from raw."""
    seen = set()  # a batch counts once however often it is replayed (G1)
    counts = Counter()
    for bid in batch_ids:
        if bid in seen:
            continue
        seen.add(bid)
        for rec in raw_records[bid]:
            _, _, _, sev, ts = raw_identity_and_event(rec)
            counts[(ts.date().isoformat(), sev)] += 1
    return dict(counts)


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_g1_replaying_a_batch_changes_nothing(replay, db, all_batch_ids):
    """G1 - Idempotent.

    Load everything once and snapshot sightings, findings and the rollup.
    Then replay a batch that is already in (a retried job) and snapshot again.
    The two snapshots must be identical in all three tables. Generic: holds for
    any dataset and any replayed batch."""
    replay(*all_batch_ids)
    once = snapshot(db)

    victim = all_batch_ids[len(all_batch_ids) // 2]
    replay(victim, reset=False)
    twice = snapshot(db)

    assert twice == once, (
        f"replaying {victim} changed the database:\n{describe_diff(once, twice)}"
    )


def test_g2_any_permutation_converges_to_the_same_database(replay, db, all_batch_ids):
    """G2 - Order-independent.

    Process the same set of batches forwards and in reverse, from a clean
    database each time. Final state must be identical, table by table.
    Generic: only compares the pipeline against itself."""
    replay(*all_batch_ids)
    forwards = snapshot(db)

    replay(*reversed(all_batch_ids))
    backwards = snapshot(db)

    assert backwards == forwards, (
        f"batch order changed the result:\n{describe_diff(forwards, backwards)}"
    )


def test_g4_g6_late_batch_never_moves_history_backwards(replay, db, all_batch_ids):
    """G4 - Monotonic history, G6 - Late arrival tolerated.

    Hold the oldest batch back, load the rest, record first/last_seen_at for
    every finding, then submit the held-back batch late. Afterwards:
      * no last_seen_at moved backwards and no first_seen_at moved forwards (G4);
      * every finding's first/last_seen_at equals min/max(detected_at) of its
        own sightings - i.e. the late batch was absorbed correctly (G6).
    The second check is a pure invariant over the database and holds on any
    dataset."""
    late, *rest = all_batch_ids
    replay(*rest)
    with db.cursor() as cur:
        cur.execute("SELECT source, asset_key, rule_key, first_seen_at, last_seen_at FROM findings")
        before = {r[:3]: r[3:] for r in cur.fetchall()}

    replay(late, reset=False)
    with db.cursor() as cur:
        cur.execute("SELECT source, asset_key, rule_key, first_seen_at, last_seen_at FROM findings")
        after = {r[:3]: r[3:] for r in cur.fetchall()}
        cur.execute("""
            SELECT f.source, f.asset_key, f.rule_key,
                   f.first_seen_at, s.first_s, f.last_seen_at, s.last_s
              FROM findings f
              JOIN (SELECT source, asset_key, rule_key,
                           min(detected_at) AS first_s, max(detected_at) AS last_s
                      FROM sightings GROUP BY 1, 2, 3) s USING (source, asset_key, rule_key)
             WHERE f.first_seen_at <> s.first_s OR f.last_seen_at <> s.last_s
        """)
        disagree = cur.fetchall()

    went_back = [(k, before[k][1], after[k][1]) for k in before if after[k][1] < before[k][1]]
    went_fwd = [(k, before[k][0], after[k][0]) for k in before if after[k][0] > before[k][0]]
    assert not went_back, (
        f"late {late}: last_seen_at moved BACKWARDS for {len(went_back)} findings, e.g. {went_back[:3]}"
    )
    assert not went_fwd, (
        f"late {late}: first_seen_at moved FORWARDS for {len(went_fwd)} findings, e.g. {went_fwd[:3]}"
    )
    assert not disagree, (
        f"{len(disagree)} findings disagree with their own sightings, e.g. {disagree[:3]}"
    )


def test_g3_every_raw_record_lands_in_sightings_exactly_once(replay, db, raw_records, all_batch_ids):
    """G3 - Complete and single-instanced (sightings side), reconciled against
    the raw files on disk, independently of the pipeline's normaliser.

    For every batch: the multiset of (batch, source, asset_key, rule_key)
    delivered must equal the multiset in sightings. Hostnames are compared
    case-insensitively so this test does not pre-judge the identity question
    raised in REPORT.md (finding 4) - it only catches drops and duplicates."""
    replay(*all_batch_ids)

    expected = Counter()
    for bid in all_batch_ids:
        for rec in raw_records[bid]:
            src, host, rule, _, _ = raw_identity_and_event(rec)
            expected[(bid, src, host.lower(), rule)] += 1

    with db.cursor() as cur:
        cur.execute("SELECT batch_id, source, lower(asset_key), rule_key FROM sightings")
        actual = Counter(cur.fetchall())

    missing = expected - actual
    extra = actual - expected
    by_shape = Counter((k[1], k[3].split("-")[0]) for k in missing.elements())
    assert not missing, (
        f"{sum(missing.values())} raw records never reached sightings "
        f"(by source/rule family: {dict(by_shape)}); e.g. {list(missing)[:3]}"
    )
    assert not extra, f"{sum(extra.values())} sightings with no raw record; e.g. {list(extra)[:3]}"


def test_g5_rollup_served_by_api_matches_raw_after_replay_and_late_arrival(
        replay, client, raw_records, all_batch_ids):
    """G5 - Rollup agrees with detail - checked end-to-end THROUGH THE API,
    i.e. what the dashboard and the compliance export actually read.

    Sequence: everything except the first batch, a replay of one batch (G1),
    then the first batch late (G6). Expected numbers are computed from the raw
    NDJSON alone, bucketed by UTC calendar day (scanner_b reports at +02:00 and
    the DB session is Asia/Jerusalem, so this also pins the UTC-day rule).
    Generic: no hard-coded numbers."""
    first, *rest = all_batch_ids
    sequence = [*rest, rest[0], first]
    replay(*sequence)

    try:
        resp = client.get("/api/rollup")
    except httpx.TransportError as exc:
        pytest.skip(f"API not reachable ({exc}); start it with `make api`")
    assert resp.status_code == 200
    served = {(r["day"], r["severity"]): r["n"] for r in resp.json()["rows"]}

    expected = expected_rollup(raw_records, sequence)
    wrong = {k: (served.get(k), expected.get(k))
             for k in sorted(set(served) | set(expected))
             if served.get(k) != expected.get(k)}
    assert not wrong, (
        "rollup served by /api/rollup disagrees with the raw batches "
        "{(day, severity): (served, expected)}: " + repr(wrong)
    )

    summary = client.get("/api/summary").json()
    assert summary["total_sightings"] == sum(expected.values())


def test_g3_one_findings_row_per_identity_hostnames_case_insensitive(replay, db, all_batch_ids):
    """G3 - Single-instanced (findings side), under the assumption that a
    hostname is case-insensitive (RFC 4343), so 'BASTION-STG-16' and
    'bastion-stg-16' from the same scanner are one asset.

    NOTE: this encodes an assumption we have asked the team to confirm
    (REPORT.md, finding 4). If the answer is 'identity is byte-exact', delete
    this test - the exact-match half of G3 is already enforced by the UNIQUE
    constraint on findings. Generic invariant: holds on any dataset."""
    replay(*all_batch_ids)
    with db.cursor() as cur:
        cur.execute("""
            SELECT source, lower(asset_key), rule_key, array_agg(asset_key ORDER BY asset_key)
              FROM findings
             GROUP BY 1, 2, 3
            HAVING count(*) > 1
        """)
        twins = cur.fetchall()
    assert not twins, (
        f"{len(twins)} finding identities are split across hostname spellings, e.g. {twins[:3]}"
    )
