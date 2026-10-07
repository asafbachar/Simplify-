# Bug report — ranked by consequence to the customer consuming the rollup

**Verdict: none of the six guarantees hold as written. Don't let the customer use the rollup for compliance reporting yet.** The per-guarantee verdicts and numbers are in `MATRIX.md`, open questions in `QUESTIONS.md`, next steps in `NEXT.md`.

Every number below comes from the CLI levers on a clean database and can be repeated exactly.

---

## 1. All CIS benchmark findings from scanner_c are dropped without any warning — Severity: Critical (G3)

**What.** `normalize_scanner_c` reads `rec["finding"]["cvss"]["base_score"]`. CIS records have no `cvss` block (a hardening check has no CVSS score), so the lookup raises `KeyError`. `normalize_batch` catches the error with `except Exception: continue`, so the record is dropped and nothing is logged. The ledger then stores the post-drop count as `record_count`, so the ledger hides the gap too.

**Repro.**
```
pipeline reset && pipeline run --batch B01
# -> "run B01: 2024 records loaded";  wc -l raw/batch_B01.ndjson -> 2092
SELECT count(*) FROM sightings WHERE source='scanner_c' AND rule_key LIKE 'CIS-%';   -- 0
```

**Size (all 12 batches).** 951 records and 120 finding identities are lost, which is 100% of scanner_c's CIS output. By severity: 130 high, 591 medium, 230 low. After `run --all` the tables show `sightings 27,113` and `findings 3,414`. The raw files say 28,064 and 3,476.

| Rollup bucket | high | medium | low |
|---|---|---|---|
| 2026-03-07 | −41 | −176 | −67 |
| 2026-03-08 | −40 (serves **2,070**, raw says **2,110**) | −193 | −80 |
| 2026-03-09 | −49 | −222 | −83 |

**Why it ranks first.** The customer wants the rollup for *compliance* reporting, and CIS benchmarks are the compliance controls. This bug under-reports, which is the dangerous direction for a compliance report. It happens on every run, today, with no replay or outage needed. Nothing detects it: no error, no log line, and the ledger agrees with the corrupted data. Note that the brief's own worked example ("totals 2,070") repeats the pipeline's wrong number.

## 2. Replaying a batch double-counts it in the rollup, permanently — Severity: Critical (G1, G5)

**What.** `REFRESH_ROLLUP` aggregates the staging table `stg` (everything delivered). It should aggregate only the rows that actually landed in `sightings`. It then *adds* the result to the existing bucket. `sightings` and `findings` are protected by `ON CONFLICT DO NOTHING`, but the rollup is not.

**Repro.**
```
pipeline reset && pipeline run --all && pipeline status    # sightings 27,113  rollup sum 27,113
pipeline run --batch B07 && pipeline status                # sightings 27,113  rollup sum 29,446
```
| 2026-03-08 | rollup | live over sightings |
|---|---|---|
| critical | 812 | 647 |
| high | 2,594 | 2,070 |
| low | 2,962 | 2,362 |
| medium | 5,199 | 4,155 |

That one retry inflates the whole day by 25% (+2,333, which is exactly B07's size). Every further retry adds the batch again. Nothing corrects it except a full `reset` and reload. `pipeline status` still reports 12 batches and nothing alerts.

**Why second, not first.** The damage is unbounded and permanent, but it needs a replay to trigger, and it over-reports. Finding 1 is happening right now and under-reports. If replays are frequent in production, these two are close to tied.

## 3. A late or out-of-order batch moves `last_seen_at` backwards, so auto-close retires live exposures — Severity: High (G2, G4, G6)

**What.** `UPSERT_FINDINGS` sets `last_seen_at = EXCLUDED.last_seen_at`, which takes the batch's value unconditionally. It should use `GREATEST(...)`. `first_seen_at` correctly uses `LEAST`. `severity` and `cvss` have the same last-writer-wins flaw. That flaw is latent: no identity in this dataset ever changes severity (QUESTIONS.md Q4).

**Repro, using the brief's worked example** (scanner_a, bastion-prd-04, CVE-2025-9906):
```
pipeline reset && pipeline run --batch B06 --batch B07 --batch B08  -> last_seen_at 2026-03-08 22:37:43Z
pipeline reset && pipeline run --batch B06 --batch B08 --batch B07  -> last_seen_at 2026-03-08 16:11:53Z
```

**Size.**
- Forward vs reverse order over all 12 batches: 3,142 of 3,414 findings have a different `last_seen_at`. `sightings` and the rollup are identical.
- `B02…B12` and then `B01` late (a collector outage): 1,970 findings move backwards to 2026-03-07. Applying the 48h auto-close rule at the latest sighting, **2,053 findings would be retired. The correct number is 268, so 1,785 live exposures would be closed.**
- `B05` late: 2,171 findings move backwards. They are not yet past 48h, so they close early on the next tick instead.

**Why third.** The rollup counts are correct here: it counts sightings, not findings. The customer's report numbers are right, but the platform's view of what is open is wrong, and in the direction of hiding exposures.

## 4. The same scanner_b finding splits into two identities on hostname case — Severity: Medium (G3, *pending QUESTIONS.md Q1*)

**What.** In B01–B06, scanner_b reports 27 hosts in upper case (`BASTION-STG-16`). From B07 on it reports them in lower case. The pipeline compares hostnames byte-for-byte, so this produces 58 duplicate findings (3,414 rows, but 3,356 distinct identities when hostname case is ignored), from 197 records. The upper-case twin stops being seen after B06 and gets auto-closed. The lower-case twin's `first_seen_at` is roughly a day too late, which understates the exposure's age and SLA.

```
SELECT source, lower(asset_key), rule_key, count(*) FROM findings GROUP BY 1,2,3 HAVING count(*) > 1;  -- 58 rows
```

**Why last.** The rollup is unaffected, and the contract defines identity as the triple without saying whether hostnames are case-sensitive. Hostnames are case-insensitive under RFC 4343, so I treat this as a bug, but the team should rule on it.
