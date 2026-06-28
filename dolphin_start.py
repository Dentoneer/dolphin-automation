"""
Dolphin Nautilus WiFi Pro - Start cleaning cycle via MyDolphin Plus cloud API.

Flow:
  1. Use refresh token → get fresh IdToken from Cognito
  2. IdToken → login to Maytronics API → get robot serial + API token
  3. Encrypt serial → get AWS IoT temp credentials
  4. Connect to AWS IoT MQTT → publish start command
"""

import os
import sys
import json
import time
import base64
import hashlib
import secrets
import logging

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
from awscrt import mqtt, auth, io
from awsiot import mqtt_connection_builder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Cognito ───────────────────────────────────────────────────────────────────
COGNITO_ENDPOINT = "https://cognito-idp.us-west-2.amazonaws.com/"
COGNITO_CLIENT_ID = "4ed12eq01o6n0tl5f0sqmkq2na"
COGNITO_HEADERS = {"Content-Type": "application/x-amz-json-1.1"}

# ── Maytronics API ────────────────────────────────────────────────────────────
BASE_URL = "https://mbapp18.maytronics.com/api"
LOGIN_URL = f"{BASE_URL}/users/Login/"
TOKEN_URL = f"{BASE_URL}/IOT/getToken_DecryptSN/"
ROBOT_DETAILS_URL = f"{BASE_URL}/serialnumbers/getrobotdetailsbymusn/"

APP_HEADERS = {
    "appkey": "346BDE92-53D1-4829-8A2E-B496014B586C",
    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
}

# ── AWS IoT ───────────────────────────────────────────────────────────────────
AWS_IOT_ENDPOINT = "a12rqfdx55bdbv-ats.iot.eu-west-1.amazonaws.com"
AWS_REGION = "eu-west-1"


def refresh_id_token(refresh_token: str) -> str:
    """Exchange a Cognito refresh token for a fresh IdToken."""
    log.info("Refreshing Cognito IdToken...")
    payload = {
        "AuthFlow": "REFRESH_TOKEN_AUTH",
        "ClientId": COGNITO_CLIENT_ID,
        "AuthParameters": {"REFRESH_TOKEN": refresh_token},
    }
    resp = requests.post(
        COGNITO_ENDPOINT,
        headers={**COGNITO_HEADERS, "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    id_token = data["AuthenticationResult"]["IdToken"]
    log.info("IdToken refreshed.")
    return id_token


def _email_from_id_token(id_token: str) -> str:
    """Decode JWT payload (no verification) to extract the email claim."""
    try:
        payload_b64 = id_token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("email", "<not found>")
    except Exception:
        return "<could not decode>"


def login(email: str, id_token: str) -> tuple[str, str]:
    """Returns (api_token, robot_serial_number)."""
    if not email:
        raise RuntimeError("DOLPHIN_EMAIL secret is empty — check your GitHub repo secrets.")
    token_email = _email_from_id_token(id_token)
    log.info("DOLPHIN_EMAIL secret : %s", email)
    log.info("Email inside IdToken : %s", token_email)
    if email.lower().strip() != token_email.lower().strip():
        log.warning("Email mismatch — these must match exactly for Maytronics login to work.")
    resp = requests.post(
        LOGIN_URL,
        headers={**APP_HEADERS, "id-token": id_token},
        data={"email": email},
        timeout=15,
    )
    if not resp.ok:
        log.error("Maytronics login HTTP %s: %s", resp.status_code, resp.text)
    resp.raise_for_status()
    data = resp.json()

    if str(data.get("Status", "0")) != "1":
        log.error("Maytronics login response: %s", data)
        raise RuntimeError(f"Login failed: {data.get('Alert', data)}")

    payload = data["Data"]
    api_token = payload["Token"]
    robot_serial = payload["Sernum"]
    log.info("Logged in. Robot serial: %s", robot_serial)
    return api_token, robot_serial


def get_motor_unit_serial(api_token: str, robot_serial: str) -> str:
    """Exchange robot serial for the motor-unit serial (used for MQTT topic)."""
    resp = requests.post(
        ROBOT_DETAILS_URL,
        headers={**APP_HEADERS, "token": api_token},
        data={"robotSerial": robot_serial},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    motor_serial = data["Data"]["MSN"]
    log.info("Motor unit serial: %s", motor_serial)
    return motor_serial


def _aes_encrypt_serial(email: str, motor_serial: str) -> str:
    """AES-128 CBC encryption used by Maytronics to generate the AWS token."""
    key = hashlib.md5(f"{email[:2]}ha".lower().encode()).digest()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(motor_serial.encode()) + padder.finalize()

    for _ in range(20):
        iv = secrets.token_bytes(16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        enc = cipher.encryptor()
        ciphertext = enc.update(padded) + enc.finalize()
        token = base64.b64encode(iv + ciphertext).decode()
        if "+" not in token:
            return token

    raise RuntimeError("Could not generate a valid AES token after 20 attempts")


def get_aws_credentials(email: str, api_token: str, motor_serial: str) -> dict:
    """Return AWS temp credentials: AccessKeyId, SecretAccessKey, SessionToken."""
    aws_token = _aes_encrypt_serial(email, motor_serial)
    resp = requests.post(
        TOKEN_URL,
        headers={**APP_HEADERS, "token": api_token},
        data={"sn": aws_token, "musn": motor_serial},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    creds = data["Data"]["Credentials"]
    log.info("Got AWS credentials.")
    return creds


def start_cleaning(motor_serial: str, creds: dict) -> None:
    """Connect to AWS IoT MQTT and publish a start-cleaning command."""
    shadow_topic = f"$aws/things/{motor_serial}/shadow/update"

    command = {
        "state": {
            "desired": {
                "system_state": {"pwsState": "on"},
                "cleaning_mode": {"mode": "regular"},
            }
        }
    }

    credentials_provider = auth.AwsCredentialsProvider.new_static(
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretAccessKey"],
        session_token=creds["SessionToken"],
    )

    event_loop_group = io.EventLoopGroup(1)
    host_resolver = io.DefaultHostResolver(event_loop_group)
    client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

    mqtt_connection = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=AWS_IOT_ENDPOINT,
        client_bootstrap=client_bootstrap,
        region=AWS_REGION,
        credentials_provider=credentials_provider,
        client_id=f"dolphin-automation-{int(time.time())}",
        clean_session=True,
        keep_alive_secs=30,
    )

    log.info("Connecting to AWS IoT...")
    mqtt_connection.connect().result(timeout=30)
    log.info("Connected. Sending start command...")

    mqtt_connection.publish(
        topic=shadow_topic,
        payload=json.dumps(command),
        qos=mqtt.QoS.AT_LEAST_ONCE,
    ).result(timeout=10)

    log.info("Command sent. Your pool robot should start shortly.")
    mqtt_connection.disconnect().result(timeout=10)


def main():
    email = os.environ["DOLPHIN_EMAIL"]
    refresh_token = os.environ["DOLPHIN_REFRESH_TOKEN"]

    try:
        id_token = refresh_id_token(refresh_token)
        api_token, robot_serial = login(email, id_token)
        motor_serial = get_motor_unit_serial(api_token, robot_serial)
        creds = get_aws_credentials(email, api_token, motor_serial)
        start_cleaning(motor_serial, creds)
    except Exception as exc:
        log.error("Failed to start robot: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
