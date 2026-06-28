"""
One-time setup: get a Maytronics refresh token via OTP.

Run this once on your computer:
  python setup_token.py

It will:
  1. Send an OTP to your MyDolphin Plus email
  2. Ask you to paste the code
  3. Print your refresh token to save as a GitHub Secret
"""

import json
import requests

COGNITO_ENDPOINT = "https://cognito-idp.us-west-2.amazonaws.com/"
COGNITO_CLIENT_ID = "4ed12eq01o6n0tl5f0sqmkq2na"

HEADERS = {
    "Content-Type": "application/x-amz-json-1.1",
}


def request_otp(email: str) -> str:
    """Trigger Cognito to send OTP email. Returns the session token."""
    payload = {
        "AuthFlow": "CUSTOM_AUTH",
        "ClientId": COGNITO_CLIENT_ID,
        "AuthParameters": {"USERNAME": email},
    }
    resp = requests.post(
        COGNITO_ENDPOINT,
        headers={**HEADERS, "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    session = data.get("Session")
    if not session:
        raise RuntimeError(f"Unexpected response: {data}")
    print("OTP sent! Check your email.")
    return session


def submit_otp(email: str, session: str, otp_code: str) -> dict:
    """Submit OTP code. Returns authentication tokens."""
    payload = {
        "ChallengeName": "CUSTOM_CHALLENGE",
        "ClientId": COGNITO_CLIENT_ID,
        "ChallengeResponses": {
            "USERNAME": email,
            "ANSWER": otp_code,
        },
        "Session": session,
    }
    resp = requests.post(
        COGNITO_ENDPOINT,
        headers={**HEADERS, "X-Amz-Target": "AWSCognitoIdentityProviderService.RespondToAuthChallenge"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    auth_result = data.get("AuthenticationResult")
    if not auth_result:
        raise RuntimeError(f"OTP rejected or unexpected response: {data}")
    return auth_result


def main():
    print("=== MyDolphin Plus - One-Time Token Setup ===\n")
    email = input("Enter your MyDolphin Plus email: ").strip()

    print(f"\nSending OTP to {email}...")
    session = request_otp(email)

    otp_code = input("Enter the OTP code from your email: ").strip()

    print("\nValidating OTP...")
    tokens = submit_otp(email, session, otp_code)

    refresh_token = tokens["RefreshToken"]
    id_token = tokens["IdToken"]

    print("\n✓ Success!\n")
    print("=" * 60)
    print("Add these as GitHub Secrets in your repo:")
    print("  Settings → Secrets and variables → Actions\n")
    print(f"DOLPHIN_EMAIL={email}")
    print(f"DOLPHIN_REFRESH_TOKEN={refresh_token}")
    print("=" * 60)
    print("\nThe refresh token does not expire as long as it's used")
    print("regularly (GitHub Actions will use it twice a day).")


if __name__ == "__main__":
    main()
