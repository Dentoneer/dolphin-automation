"""
Dolphin Nautilus WiFi Pro - Start cleaning cycle via MyDolphin Plus cloud API.

Flow:
  1. Use refresh token → get fresh IdToken from Cognito
  2. IdToken → authenticate with Maytronics API → get robot serial + API token
  3. IdToken → get AWS IoT temp credentials
  4. Connect to AWS IoT MQTT → publish start command
"""

import os
import sys
import json
import time
import logging

import requests
from awscrt import mqtt, auth, io
from awsiot import mqtt_connection_builder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

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

# ── AWS IoT ───────────────────────────────────────────────────────────────────
AWS_IOT_ENDPOINT = "a12rqfdx55bdbv-ats.iot.eu-west-1.amazonaws.com"
AWS_REGION = "eu-west-1"


def refresh_id_token(refresh_token: str) -> str:
    """Exchange a Cognito refresh token for a fresh IdToken."""
    log.info("Refreshing Cognito IdToken...")
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
    id_token = resp.json()["AuthenticationResult"]["IdToken"]
    log.info("IdToken refreshed.")
    return id_token


def authenticate(id_token: str) -> str:
    """Authenticate with Maytronics. Returns motor_unit_serial."""
    log.info("Authenticating with Maytronics API...")
    resp = requests.post(
        AUTH_URL,
        headers={**BEARER_HEADERS, "Authorization": f"Bearer {id_token}"},
        data="",
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if str(data.get("Status", "0")) != "1":
        log.error("Maytronics auth response: %s", data)
        raise RuntimeError(f"Authentication failed: {data.get('Alert', data)}")
    motor_serial = data["Data"]["eSERNUM"]
    log.info("Authenticated. Motor unit serial: %s", motor_serial)
    return motor_serial


def get_aws_credentials(id_token: str) -> dict:
    """Fetch temporary AWS IoT credentials."""
    log.info("Fetching AWS credentials...")
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
    log.info("Got AWS credentials.")
    return creds


def start_cleaning(motor_serial: str, creds: dict) -> None:
    """Connect to AWS IoT MQTT and publish a start-cleaning command."""
    shadow_topic = f"$aws/things/{motor_serial}/shadow/update"
    command = {
        "state": {
            "desired": {
                "systemState": {"pwsState": "on"},
                "cleaningMode": {"mode": "all"},
            }
        }
    }

    ca_path = os.path.join(os.path.dirname(__file__), "AmazonRootCA.pem")
    with open(ca_path, "rb") as f:
        ca_bytes = f.read()

    credentials_provider = auth.AwsCredentialsProvider.new_static(
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretAccessKey"],
        session_token=creds.get("Token", ""),
    )

    event_loop_group = io.EventLoopGroup(1)
    host_resolver = io.DefaultHostResolver(event_loop_group)
    client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

    mqtt_connection = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=AWS_IOT_ENDPOINT,
        port=443,
        client_bootstrap=client_bootstrap,
        region=AWS_REGION,
        ca_bytes=ca_bytes,
        credentials_provider=credentials_provider,
        client_id=f"dolphin-automation-{int(time.time())}",
        clean_session=False,
        keep_alive_secs=30,
    )

    log.info("Connecting to AWS IoT...")
    mqtt_connection.connect().result(timeout=30)
    log.info("Connected. Sending start command...")

    publish_future, _ = mqtt_connection.publish(
        topic=shadow_topic,
        payload=json.dumps(command),
        qos=mqtt.QoS.AT_LEAST_ONCE,
    )
    publish_future.result(timeout=10)

    log.info("Command sent. Your pool robot should start shortly.")
    mqtt_connection.disconnect().result(timeout=10)


def main():
    refresh_token = os.environ["DOLPHIN_REFRESH_TOKEN"]

    try:
        id_token = refresh_id_token(refresh_token)
        motor_serial = authenticate(id_token)
        creds = get_aws_credentials(id_token)
        start_cleaning(motor_serial, creds)
    except Exception as exc:
        log.error("Failed to start robot: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
