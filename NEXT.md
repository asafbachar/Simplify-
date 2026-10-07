# What next

## What three more hours would buy

- **Property-based ordering tests.** Run random permutations, random duplicate insertions and random hold-backs (`hypothesis`, seeded) against the G4/G5/G6 invariants, instead of the few fixed sequences I ran.
- **A check of production before go-live.** Run the G5 drift query and the `findings` vs `sightings` invariant on the live database. Replays have already happened there, so the live rollup is very likely already inflated, and the customer needs a rebuild before they consume it.
- **Malformed-input coverage per source shape.** Missing fields, nulls, extra fields and bad timestamps for each of the three payload shapes. Finding 1 shows normalisation is where data silently disappears.
- **Concurrency.** Two `pipeline run` processes at the same time (an overlapping retry). The load is one transaction per batch, but the rollup's read-modify-write under concurrency is untested.
- **Timezone edges.** Israel switches to daylight saving time on 2026-03-27, and every batch here falls before that. A batch spanning the switch, plus scanner_b's fixed `+02:00` offset, deserves an explicit UTC-bucket test.

## What I deliberately did not test

- **The dashboard in a browser (Playwright).** It renders the same `/api/rollup` and `/api/summary` the API test already checks. Every bug found is in the data, not in rendering, so a browser test would add a dependency and flakiness for little coverage.
- **API filters and pagination** (`/api/findings?source=…&limit=…`). They're plain parameterised SQL over `findings`, and their correctness depends on the `findings` table, which the contract tests already cover.
- **Performance and volume.** Twelve batches of about 2,400 rows don't tell us anything about production scale.
- **Schema migration and upgrade paths.** `pipeline reset` drops and recreates everything, and the contract doesn't promise anything about upgrades.

## The one CI gate I'd add first

**A required CI job that runs `make test` (the contract suite) against a fresh Postgres whose session timezone is *not* UTC, on every pull request, and blocks the merge on red.**

Two tests in it are cheap and catch whole classes of regression, not just today's bugs:
- the replay test (G1);
- the rollup-vs-detail invariant (G5).

Running with a non-UTC timezone keeps the UTC-day rule honest.
