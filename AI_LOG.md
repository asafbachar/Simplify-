# AI log

**How I used it.** An LLM assistant (Claude) profiled the 28,064 raw records, ran the replay, permutation and late-arrival sequences, drafted the six pytest tests and the write-ups, and wrote a one-command run script for my WSL machine. I reviewed each claim against a run, and re-ran everything on my own machine with the same results.

**What it accelerated.** Reconciling the raw files independently of the pipeline, and turning each finding into a reproducible sequence with real numbers. It also found the silent CIS drop (951 records), which reading the code alone would easily have missed.

**What it got wrong, and I caught:**
- **It couldn't install the right Python on my machine.** Its setup script assumed a recent Ubuntu. My WSL runs Ubuntu 20.04 with Python 3.8, and the project needs 3.10 or newer. Its first fix, the deadsnakes PPA, no longer supports 20.04 and failed. I found we had to override the system Python: install a standalone Python 3.11 next to it with `uv`, and point the virtualenv at that. The virtualenv also had to be built inside Linux (`~/.venvs`), because Python can't create one on the Windows `D:` drive.
- **Its fix for the pipeline was too slow.** The fixed version took more than a minute. When I pushed back, profiling showed it looked up `cvss` row by row (a quadratic `LATERAL` subquery). Rewritten as one join, the suite went from about 48 s to 21 s, with byte-identical database output.
- **It nearly accepted a wrong number.** It first read the brief's worked example ("2,070") as confirmation that the rollup was right. Reconciling against the raw files showed 2,110: the brief repeats the buggy pipeline's own output.

**Conclusion.** The assistant was very good at breadth: profiling, experiments and first drafts. But every claim it made needed a reproduction, and its assumptions about the environment needed testing on the real machine. The bugs it introduced (slow SQL, a broken setup) only surfaced when its output was actually run, which is the point of this assignment.
