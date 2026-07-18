"""
Checks the robot's current shadow state 2 hours after a start command.
Updates run_log.json with stop time, duration, and status.
Then regenerates dashboard.html with the latest data embedded.
"""

import json
import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from awscrt import mqtt, auth, io
from awsiot import mqtt_connection_builder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

LOG_FILE = Path(__file__).parent / "run_log.json"
DASHBOARD_FILE = Path(__file__).parent / "dashboard.html"

# ── Cognito ───────────────────────────────────────────────────────────────────
COGNITO_ENDPOINT = "https://cognito-idp.us-west-2.amazonaws.com/"
COGNITO_CLIENT_ID = "4ed12eq01o6n0tl5f0sqmkq2na"

# ── Maytronics API ────────────────────────────────────────────────────────────
APPS_BASE_URL = "https://apps.maytronics.com"
AUTH_URL = f"{APPS_BASE_URL}/mobapi/user/authenticate-user/"
AWS_TOKEN_URL = f"{APPS_BASE_URL}/mt-sso/aws/getToken/"

BEARER_HEADERS = {
    "AppKey": "346BDE92-53D1-4829-8A2E-B496014B586C",
    "app_version": "ios_3.1.7_2",
    "Accept": "*/*",
}

AWS_IOT_ENDPOINT = "a12rqfdx55bdbv-ats.iot.eu-west-1.amazonaws.com"
AWS_REGION = "eu-west-1"


def refresh_id_token(refresh_token: str) -> str:
    resp = requests.post(
        COGNITO_ENDPOINT,
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        },
        json={
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": refresh_token},
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["AuthenticationResult"]["IdToken"]


def authenticate(id_token: str) -> str:
    resp = requests.post(
        AUTH_URL,
        headers={**BEARER_HEADERS, "Authorization": f"Bearer {id_token}"},
        data="",
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if str(data.get("Status", "0")) != "1":
        raise RuntimeError(f"Authentication failed: {data.get('Alert', data)}")
    return data["Data"]["eSERNUM"]


def get_aws_credentials(id_token: str) -> dict:
    resp = requests.get(
        AWS_TOKEN_URL,
        headers={**BEARER_HEADERS, "Authorization": f"Bearer {id_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    creds = data.get("Data", data)
    if not creds.get("AccessKeyId"):
        raise RuntimeError(f"Failed to get AWS credentials: {data}")
    return creds


def get_shadow_state(motor_serial: str, creds: dict) -> dict:
    """Fetch the robot's current reported shadow state."""
    shadow_get_topic = f"$aws/things/{motor_serial}/shadow/get"
    shadow_accepted_topic = f"$aws/things/{motor_serial}/shadow/get/accepted"

    received = {}
    event = {"done": False}

    def on_message(topic, payload, **kwargs):
        received.update(json.loads(payload))
        event["done"] = True

    credentials_provider = auth.AwsCredentialsProvider.new_static(
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretAccessKey"],
        session_token=creds.get("Token", ""),
    )

    event_loop_group = io.EventLoopGroup(1)
    host_resolver = io.DefaultHostResolver(event_loop_group)
    client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

    ca_path = os.path.join(os.path.dirname(__file__), "AmazonRootCA.pem")
    with open(ca_path, "rb") as f:
        ca_bytes = f.read()

    mqtt_connection = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=AWS_IOT_ENDPOINT,
        port=443,
        client_bootstrap=client_bootstrap,
        region=AWS_REGION,
        ca_bytes=ca_bytes,
        credentials_provider=credentials_provider,
        client_id=f"dolphin-check-{int(time.time())}",
        clean_session=True,
        keep_alive_secs=30,
    )

    mqtt_connection.connect().result(timeout=30)

    # Newer awsiot returns (future, packet_id) — unpack and wait on the future only
    sub_future, _ = mqtt_connection.subscribe(
        topic=shadow_accepted_topic,
        qos=mqtt.QoS.AT_LEAST_ONCE,
        callback=on_message,
    )
    sub_future.result(timeout=10)

    pub_future, _ = mqtt_connection.publish(
        topic=shadow_get_topic,
        payload="{}",
        qos=mqtt.QoS.AT_LEAST_ONCE,
    )
    pub_future.result(timeout=10)

    # Wait up to 10 seconds for the shadow response
    for _ in range(20):
        if event["done"]:
            break
        time.sleep(0.5)

    mqtt_connection.disconnect().result(timeout=10)
    return received


def determine_stop_time(shadow: dict, start_time: datetime) -> tuple[datetime, str]:
    """
    Extract when the robot stopped from shadow metadata timestamps.
    Falls back to now if no timestamp available.
    """
    try:
        # Shadow metadata has Unix timestamps for each reported field
        pws_ts = (
            shadow.get("metadata", {})
            .get("reported", {})
            .get("systemState", {})
            .get("pwsState", {})
            .get("timestamp")
        )
        if pws_ts:
            stop_time = datetime.fromtimestamp(pws_ts, tz=timezone.utc)
            # Only trust it if it's after the start time
            if stop_time > start_time:
                return stop_time, "stopped_early"
    except Exception:
        pass

    # If the robot is still reported as "on" after 2 hours, it completed normally
    pws_state = (
        shadow.get("state", {})
        .get("reported", {})
        .get("systemState", {})
        .get("pwsState", "")
    )
    now = datetime.now(timezone.utc)
    if str(pws_state).lower() in ("on", "1"):
        return now, "completed"
    return now, "stopped_early"


def update_log(shadow: dict) -> list:
    """Find the most recent 'running' entry and update it with stop info."""
    log_data = json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else []

    # Find the last running entry
    entry = next((e for e in reversed(log_data) if e["status"] == "running"), None)
    if not entry:
        log.warning("No running entry found in log — nothing to update.")
        return log_data

    start_time = datetime.fromisoformat(entry["start_time"])
    stop_time, status = determine_stop_time(shadow, start_time)
    duration = round((stop_time - start_time).total_seconds() / 60)

    entry["stop_time"] = stop_time.isoformat()
    entry["duration_minutes"] = duration
    entry["status"] = status

    LOG_FILE.write_text(json.dumps(log_data, indent=2))
    log.info("Updated entry #%s: %s, %d min", entry["id"], status, duration)
    return log_data


def generate_dashboard(log_data: list) -> None:
    """Write a self-contained HTML dashboard with the log data embedded."""
    rows = ""
    for entry in reversed(log_data):
        start = entry.get("start_time", "")
        stop = entry.get("stop_time", "—")
        duration = entry.get("duration_minutes")
        status = entry.get("status", "unknown")

        # Format times for display
        try:
            start_dt = datetime.fromisoformat(start)
            start_display = start_dt.strftime("%b %d, %Y %I:%M %p UTC")
        except Exception:
            start_display = start

        try:
            stop_dt = datetime.fromisoformat(stop)
            stop_display = stop_dt.strftime("%I:%M %p UTC")
        except Exception:
            stop_display = stop

        duration_display = f"{duration} min" if duration is not None else "—"

        if status == "completed":
            badge = '<span class="badge completed">✅ Completed</span>'
        elif status == "stopped_early":
            badge = '<span class="badge early">⚠️ Early Stop</span>'
        else:
            badge = '<span class="badge running">🔄 Running</span>'

        rows += f"""
        <tr>
            <td>#{entry.get('id', '?')}</td>
            <td>{start_display}</td>
            <td>{stop_display}</td>
            <td>{duration_display}</td>
            <td>{badge}</td>
        </tr>"""

    total = len(log_data)
    completed = sum(1 for e in log_data if e.get("status") == "completed")
    early = sum(1 for e in log_data if e.get("status") == "stopped_early")
    avg_duration = (
        round(sum(e["duration_minutes"] for e in log_data if e.get("duration_minutes")) / total)
        if total else 0
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🐬 Dolphin Pool Robot Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f172a; color: #e2e8f0; min-height: 100vh; padding: 2rem; }}
    h1 {{ font-size: 1.8rem; margin-bottom: 0.25rem; }}
    .subtitle {{ color: #64748b; margin-bottom: 2rem; font-size: 0.9rem; }}
    .stats {{ display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap; }}
    .stat {{ background: #1e293b; border-radius: 12px; padding: 1.25rem 1.75rem;
             flex: 1; min-width: 140px; }}
    .stat .label {{ font-size: 0.75rem; color: #64748b; text-transform: uppercase;
                    letter-spacing: 0.05em; margin-bottom: 0.5rem; }}
    .stat .value {{ font-size: 2rem; font-weight: 700; }}
    .completed-color {{ color: #34d399; }}
    .early-color {{ color: #fbbf24; }}
    table {{ width: 100%; border-collapse: collapse; background: #1e293b;
             border-radius: 12px; overflow: hidden; }}
    th {{ background: #0f172a; padding: 0.875rem 1rem; text-align: left;
          font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
          color: #64748b; }}
    td {{ padding: 0.875rem 1rem; border-bottom: 1px solid #0f172a; font-size: 0.9rem; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #263348; }}
    .badge {{ padding: 0.25rem 0.75rem; border-radius: 999px; font-size: 0.8rem;
              font-weight: 600; white-space: nowrap; }}
    .badge.completed {{ background: #064e3b; color: #34d399; }}
    .badge.early {{ background: #451a03; color: #fbbf24; }}
    .badge.running {{ background: #1e3a5f; color: #60a5fa; }}
    .updated {{ color: #475569; font-size: 0.75rem; margin-top: 1.5rem; text-align: right; }}
  </style>
</head>
<body>
  <h1>🐬 Dolphin Pool Robot</h1>
  <p class="subtitle">Run history — auto-updated after each cycle</p>

  <div class="stats">
    <div class="stat">
      <div class="label">Total Runs</div>
      <div class="value">{total}</div>
    </div>
    <div class="stat">
      <div class="label">Completed</div>
      <div class="value completed-color">{completed}</div>
    </div>
    <div class="stat">
      <div class="label">Early Stops</div>
      <div class="value early-color">{early}</div>
    </div>
    <div class="stat">
      <div class="label">Avg Duration</div>
      <div class="value">{avg_duration}<span style="font-size:1rem;color:#64748b"> min</span></div>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Started</th>
        <th>Stopped</th>
        <th>Duration</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>

  <p class="updated">Last updated: {datetime.now(timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")}</p>
</body>
</html>"""

    DASHBOARD_FILE.write_text(html)
    log.info("Dashboard regenerated.")


def main():
    refresh_token = os.environ["DOLPHIN_REFRESH_TOKEN"]

    try:
        id_token = refresh_id_token(refresh_token)
        motor_serial = authenticate(id_token)
        creds = get_aws_credentials(id_token)
        shadow = get_shadow_state(motor_serial, creds)
        log_data = update_log(shadow)
        generate_dashboard(log_data)
    except Exception as exc:
        log.error("Status check failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
