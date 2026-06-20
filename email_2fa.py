#!/usr/bin/env python3
"""Email 2FA backend — OTP generation, challenge store, Gmail API sender.

This module provides:
- generate_challenge(email) -> challenge_id
- verify_otp(challenge_id, code) -> bool
- send_otp(challenge_id) -> None (sends email via Gmail API)

Challenges are stored in memory with a 5-minute TTL.
"""

import os
import random
import time
from email.mime.text import MIMEText
from pathlib import Path

# ── Challenge Store ─────────────────────────────────────────────
# In-memory store: challenge_id -> {code, email, expires}
_pending_challenges: dict[str, dict] = {}

# Default token file path (can be overridden in tests)
TOKEN_FILE = Path(os.path.expanduser("~/.hermes/google_token.json"))

# Default sender email
SENDER_EMAIL = "kevin.douglas.disher@gmail.com"

# Challenge TTL in seconds
CHALLENGE_TTL = 300  # 5 minutes


def generate_challenge(email: str) -> str:
    """Generate a new OTP challenge for the given email.

    Returns a challenge_id that can be used with verify_otp() and send_otp().
    The OTP code is stored in the pending challenges dict.
    """
    import secrets
    challenge_id = secrets.token_hex(16)
    code = f"{random.randint(0, 999999):06d}"
    _pending_challenges[challenge_id] = {
        "code": code,
        "email": email,
        "expires": time.time() + CHALLENGE_TTL,
    }
    return challenge_id


def verify_otp(challenge_id: str, code: str) -> bool:
    """Verify an OTP code against a challenge.

    Returns True if the code matches and the challenge hasn't expired.
    The challenge is consumed (deleted) on successful verification.
    """
    challenge = _pending_challenges.get(challenge_id)
    if challenge is None:
        return False
    if time.time() > challenge["expires"]:
        # Clean up expired challenge
        _pending_challenges.pop(challenge_id, None)
        return False
    if challenge["code"] != code:
        return False
    # Consume the challenge (single-use)
    _pending_challenges.pop(challenge_id, None)
    return True


def send_otp(challenge_id: str) -> None:
    """Send the OTP code for a challenge via Gmail API.

    Raises KeyError if the challenge_id doesn't exist.
    """
    challenge = _pending_challenges[challenge_id]  # KeyError if missing
    code = challenge["code"]
    to_email = challenge["email"]

    service = _load_gmail_service()
    message = _build_message(to_email, code)
    service.users().messages().send(userId="me", body=message).execute()


def _build_message(to_email: str, code: str) -> dict:
    """Build a Gmail API message dict with the OTP code."""
    import base64

    subject = f"Your Hermes Companion verification code: {code}"
    body = (
        f"Your verification code is: {code}\n\n"
        f"This code expires in 5 minutes.\n"
        f"If you didn't request this, you can safely ignore this email.\n"
    )

    msg = MIMEText(body)
    msg["to"] = to_email
    msg["from"] = SENDER_EMAIL
    msg["subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_string().encode()).decode()
    return {"raw": raw}


def _load_gmail_service():
    """Load the Gmail API service using the OAuth token file.

    Returns a googleapiclient.discovery.Resource for the Gmail API.
    """
    import json

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"Gmail OAuth token file not found: {TOKEN_FILE}. "
            f"Run the Gmail OAuth flow first."
        )

    with open(TOKEN_FILE) as f:
        token_data = json.load(f)

    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes"),
    )

    return build("gmail", "v1", credentials=creds)
