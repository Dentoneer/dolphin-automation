# dolphin-automation

## Purpose

Automates a Maytronics Dolphin Nautilus WiFi Pro pool-cleaning robot: starts it on a
schedule (with a weather-based skip on cooler days), checks whether it actually ran,
and publishes a run-history dashboard. This exists so the robot runs without anyone
opening the MyDolphin Plus app — GitHub Actions is the "person" pressing start.

## Architecture

The robot has no local API; everything goes through Maytronics' cloud, replicating
what the official app does:

```
Cognito refresh token → IdToken → Maytronics auth (get robot serial + API token)
                                 → temp AWS IoT credentials
                                 → AWS IoT MQTT (device shadow) → robot
```

Key files:

- `dolphin_start.py` — publishes a "start" command to the robot's AWS IoT device
  shadow. Run by the `start-robot` job.
- `check_status.py` — ~2h after a start, reads the robot's shadow state back to
  determine if/when it stopped, updates `run_log.json`, and regenerates
  `dashboard.html` (embeds Chart.js charts + a run table) from that log. Run by the
  `check-robot` job.
- `log_start.py` — appends a `{status: "running"}` entry to `run_log.json`
  immediately after a successful start.
- `check_weather.py` — fetches current temp from Open-Meteo (no API key) and decides
  whether the scheduled run should proceed, via `should_run` GitHub Actions output.
- `setup_token.py` — one-time interactive script to obtain the Cognito refresh token
  via OTP login. Not run by automation.
- `run_log.json` — the persistent run history (source of truth for the dashboard).
- `dashboard.html` — generated output, committed to the repo and served live via
  **GitHub Pages** (root of `main`) at `https://dentoneer.github.io/dolphin-automation/`.
  Don't hand-edit; it's overwritten by `check_status.py` every check run.
- `.github/workflows/schedule.yml` — the cron schedule and both jobs.
- `AmazonRootCA.pem` — CA cert required for the AWS IoT TLS/MQTT connection.

`dolphin_start.py` and `check_status.py` duplicate the same Cognito/Maytronics/AWS
auth flow (not shared into a common module) — if you fix a bug in one, check the other.

## Conventions

- **Run**: not meant to be run locally in normal use — it's driven entirely by the
  GitHub Actions workflow. `setup_token.py` is the one script a human runs directly,
  once, for initial/repeat setup.
- **Secrets/sensitive files** — never print, log, or commit contents of:
  - `AmazonRootCA.pem` (public CA cert, low sensitivity, but treat as fixed
    infrastructure — don't modify)
  - `DOLPHIN_REFRESH_TOKEN` and `DOLPHIN_EMAIL` (GitHub Actions **Secrets**, not in
    the repo — obtained via `setup_token.py`)
  - `WEATHER_LAT` / `WEATHER_LON` (GitHub Actions **Variables**, non-sensitive)
- **Schedule** (Central Time, defined as UTC crons in `schedule.yml`): start attempts
  at 2 AM (always), noon (only if >80°F), 6 PM (only if >90°F); a status check runs
  ~2h after each start. 9 PM is handled by the Dolphin app's own built-in schedule,
  not this repo. Manual runs via the Actions tab always execute regardless of weather.
- Both workflow jobs `git commit && git push` their own results (`run_log.json`,
  `dashboard.html`) back to `main` using the `github-actions` bot identity — expect
  frequent bot commits in `git log`.

## Key facts / gotchas

- External services: AWS Cognito (`us-west-2`) for auth, Maytronics' own cloud API
  (`apps.maytronics.com`) for device lookup, AWS IoT Core (`eu-west-1`) for the actual
  MQTT command, Open-Meteo for weather. No official public Dolphin API — this reverse-
  engineers the MyDolphin Plus app's traffic.
- The refresh token does **not** expire as long as the workflow runs at least once
  every 30 days (per README) — if the schedule is ever paused for a month+, expect to
  redo `setup_token.py`.
- The robot has no "stop" command from this repo — it always runs its own configured
  cleaning cycle and stops on its own; `check_status.py` only *observes* that via the
  device shadow's `pwsState` timestamp, it never controls duration.
- "Stopped early" (<30 min runtime) vs "completed" is inferred, not authoritative —
  see `determine_stop_time()` in `check_status.py`.
- This local clone can silently drift behind `origin/main` for long periods since the
  bot pushes directly to `main` every few hours — run `git fetch` before trusting
  `git log` here.
