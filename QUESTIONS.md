# Questions for the pipeline's authors

Things the contract doesn't settle. I'd ask these before filing, rather than guess in a bug report.

1. **Are hostnames in `asset_key` case-insensitive?** In B01–B06, scanner_b reports 27 hosts in upper case (`BASTION-STG-16`); from B07 on it reports them in lower case. Byte-exact comparison produces 58 duplicate identities. DNS names are case-insensitive (RFC 4343), so I treated this as a bug (REPORT.md finding 4). If identity is byte-exact by design, the second G3 test should be deleted.
2. **What should happen to a record that can't be parsed?** Today it is dropped with no log line (finding 1). Should the whole batch be rejected, or should the record be quarantined and the rest loaded? Should `processed_batches` record *delivered* and *accepted* counts separately? Today `record_count` is the post-drop count, so the ledger hides the gap.
3. **Is scanner_c's `first_observed` really the detection time?** The name says "first observed", but the value advances in every batch for the same finding, so I treated it as the detection time. If it really is "first observed", scanner_c's `last_seen_at` means nothing.
4. **When severity or CVSS changes between sightings, which wins: the latest sighting or the highest ever?** The code takes whichever batch was processed last, which makes the result depend on order (G2). No identity in this dataset changes severity, so I can't demonstrate the impact, but the contract should say.
5. **Can one batch contain the same identity twice?** `UNIQUE (batch_id, source, asset_key, rule_key)` silently keeps one of them. Does "every record exactly once" (G3) mean both should be kept? This dataset has no such case.
6. **Where does the 2,070 in the brief's worked example come from?** It matches the pipeline's output for 2026-03-08/high. The raw files say 2,110, because 40 CIS records are dropped (finding 1). Were other documents, fixtures or dashboards checked against pipeline output rather than the raw data?
7. **How late can a "late" batch be (G6)?** If auto-close has already run between the original window and the late arrival, should the late batch reopen findings it has evidence for?
