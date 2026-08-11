_Last updated: 2026-08-10_

## Status

Working and actively running. GitHub Actions confirms both `start-robot` and
`check-robot` jobs completing successfully on schedule as of today (last run:
2026-08-10T23:30 UTC, entry #100, status "running" — check job for it hasn't fired
yet). Dashboard is live at `https://dentoneer.github.io/dolphin-automation/` via
GitHub Pages and updates itself after each check run.

Recent history (from `origin/main`'s `run_log.json`, 100 entries total) shows a mix
of `completed` (~105-165 min, matches full cleaning cycles) and `stopped_early`
(5-20 min) runs — early stops look frequent lately (2026-08-08/09/10 alone) but
that's expected/observed behavior, not necessarily a bug; see `determine_stop_time()`
gotcha in CLAUDE.md.

**Important**: this local clone is 124 commits behind `origin/main` (bot pushes
`run_log.json`/`dashboard.html` every few hours). Local `run_log.json` only has 31
entries dated up to 2026-07-23; the real current state lives on `origin/main`. Run
`git pull` before trusting local files for anything time-sensitive.

## Recent decisions

- 2026-07-22/23: rewrote `check_status.py`'s stop-detection logic, added stale-entry
  sweeping (running >4h with no check → auto-marked completed), and added the
  Chart.js dashboard. Older log entries (IDs 1-31, before this fix) may have
  `stopped_early` labels from the old, buggier logic — don't fully trust status
  labels on runs before 2026-07-23.
- Auth/AWS logic is intentionally duplicated between `dolphin_start.py` and
  `check_status.py` rather than shared — no shared module exists yet.

## Open threads / next steps

- No open bugs identified from the code/logs as of this check. The workflow is
  healthy and unattended.
- Consider deduping the Cognito/Maytronics/AWS-IoT auth code shared by
  `dolphin_start.py` and `check_status.py` into one module if it needs to change again.
- If early-stop frequency (see above) is unexpected/undesired, worth investigating
  whether it reflects real robot behavior (e.g., pool sensor faults, low water) vs.
  a shadow-timestamp detection issue in `check_status.py`.
