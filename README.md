# dolphin-automation

Automatically starts your Maytronics Dolphin pool robot on a schedule using GitHub Actions.

The script authenticates through AWS Cognito and the MyDolphin Plus cloud API, then sends a start command via AWS IoT MQTT — the same path the official app uses.

## How it works

1. A stored Cognito refresh token is exchanged for a short-lived IdToken
2. The IdToken logs into the Maytronics API to get the robot serial and an API token
3. The API token fetches temporary AWS IoT credentials
4. The script connects to AWS IoT over WebSocket MQTT and publishes a start command to the robot's device shadow

## Setup

### 1. Get your refresh token (one time)

```bash
pip install requests
python setup_token.py
```

Enter your MyDolphin Plus email, paste the OTP code from your inbox, and copy the printed `DOLPHIN_REFRESH_TOKEN`.

### 2. Add GitHub Secrets and Variables

Go to your repo → **Settings → Secrets and variables → Actions**.

**Secrets** (sensitive — keep private):

| Secret name | Value |
|---|---|
| `DOLPHIN_EMAIL` | Your MyDolphin Plus email |
| `DOLPHIN_REFRESH_TOKEN` | The token from step 1 |

**Variables** (non-sensitive — used for weather):

| Variable name | Value |
|---|---|
| `WEATHER_LAT` | Your latitude (e.g. `32.7767`) |
| `WEATHER_LON` | Your longitude (e.g. `-96.7970`) |

> Find your coordinates at [maps.google.com](https://maps.google.com) — right-click your location and copy the coordinates.

### 3. Schedule and weather thresholds

The robot runs on this schedule (Central Time):

| Time | Condition |
|---|---|
| 9:00 PM | Always |
| 2:00 AM | Always |
| Noon | Only if temperature > 80°F |
| 6:00 PM | Only if temperature > 90°F |

Temperature is fetched from [Open-Meteo](https://open-meteo.com/) (free, no API key needed).

You can also trigger it anytime from the **Actions** tab → "Run workflow" — manual triggers always run regardless of temperature.

## Files

| File | Purpose |
|---|---|
| `setup_token.py` | One-time setup: gets your refresh token via OTP |
| `dolphin_start.py` | Starts the robot (run by GitHub Actions) |
| `check_weather.py` | Fetches current temperature and decides whether to run |
| `.github/workflows/schedule.yml` | Cron schedule and workflow definition |
| `requirements.txt` | Python dependencies |

## Notes

- The refresh token does not expire as long as GitHub Actions uses it regularly (at least once every 30 days).
- The robot runs for its configured cleaning cycle duration and stops on its own — no stop command is needed.
- `awscrt` and `awsiot` are the official AWS IoT Device SDK v2 packages.
