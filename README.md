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

### 2. Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name | Value |
|---|---|
| `DOLPHIN_EMAIL` | Your MyDolphin Plus email |
| `DOLPHIN_REFRESH_TOKEN` | The token from step 1 |

### 3. Customize the schedule

Edit [`.github/workflows/schedule.yml`](.github/workflows/schedule.yml) and adjust the cron lines. The defaults run at **8:00 AM and 4:00 PM Eastern Time** every day.

```yaml
- cron: '0 12 * * *'   # 8:00 AM ET (UTC-4, summer)
- cron: '0 20 * * *'   # 4:00 PM ET (UTC-4, summer)
```

You can also trigger it manually from the **Actions** tab using the "Run workflow" button.

## Files

| File | Purpose |
|---|---|
| `setup_token.py` | One-time setup: gets your refresh token via OTP |
| `dolphin_start.py` | Starts the robot (run by GitHub Actions) |
| `.github/workflows/schedule.yml` | Cron schedule and workflow definition |
| `requirements.txt` | Python dependencies |

## Notes

- The refresh token does not expire as long as GitHub Actions uses it regularly (at least once every 30 days).
- The robot runs for its configured cleaning cycle duration and stops on its own — no stop command is needed.
- `awscrt` and `awsiot` are the official AWS IoT Device SDK v2 packages.
