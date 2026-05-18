"""
Magic-link OTP + session for the bewoners-portaal.

Bewoners log in by typing their email; the server checks the whitelist
(allowed_residents in db.py), generates a 6-digit code, mails it via
Resend, and the bewoner types it back in. On success, a signed cookie
keeps them logged in for 30 days rolling.

Sessions are stateless: just a signed payload in a cookie. No table to
look up on every request, no garbage collection. The session_serializer
is salted independently from the QR token so leaking one secret does
not compromise the other.
"""
from __future__ import annotations

import random
import string
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable, Optional

from flask import current_app, redirect, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import db

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OTP_TTL_MINUTES = 15

BEWONER_SESSION_COOKIE = "bewoner_session"
# 30-day rolling sessions; renewed on each set_bewoner_session_cookie call
BEWONER_SESSION_MAX_AGE = 60 * 60 * 24 * 30


# ---------------------------------------------------------------------------
# OTP
# ---------------------------------------------------------------------------

def generate_otp_code() -> str:
    """6-digit numeric code. Easy to read aloud and to type on mobile."""
    return "".join(random.choices(string.digits, k=6))


def create_and_save_otp(email: str) -> str:
    """Generate a fresh OTP, store it, and return the plaintext code so
    the caller can mail it to the bewoner."""
    code = generate_otp_code()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)
    ).strftime("%Y-%m-%d %H:%M:%S")
    db.save_bewoner_otp(email, code, expires_at)
    return code


def verify_and_consume_otp(email: str, code: str) -> bool:
    """Return True if the code matches, is unused, and unexpired.
    Marks the code as used on success."""
    if not email or not code or len(code) != 6 or not code.isdigit():
        return False
    row = db.get_valid_bewoner_otp(email, code)
    if not row:
        return False
    db.mark_bewoner_otp_used(row["id"])
    return True


# ---------------------------------------------------------------------------
# Session cookie
# ---------------------------------------------------------------------------

def _session_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=current_app.config["SECRET_KEY"],
        salt="bewoner-session-v1",
    )


def _sign_bewoner_session(email: str) -> str:
    return _session_serializer().dumps({"email": email.strip().lower()})


def get_current_bewoner_email() -> Optional[str]:
    """Return the email stored in the current bewoner_session cookie,
    or None if the cookie is missing, invalid, or expired."""
    raw = request.cookies.get(BEWONER_SESSION_COOKIE)
    if not raw:
        return None
    try:
        payload = _session_serializer().loads(
            raw, max_age=BEWONER_SESSION_MAX_AGE
        )
        email = payload.get("email")
        return email if email else None
    except (BadSignature, SignatureExpired, Exception):
        return None


def has_valid_bewoner_session() -> bool:
    """Cookie present, signature valid, not expired, AND email still on
    the whitelist. Removing a bewoner from allowed_residents immediately
    invalidates their session, even if the cookie hasn't expired yet."""
    email = get_current_bewoner_email()
    if not email:
        return False
    return db.is_email_allowed(email)


def set_bewoner_session_cookie(response, email: str):
    """Attach a freshly signed bewoner_session cookie to the response.

    HttpOnly: not readable from JavaScript (XSS defense).
    Secure: HTTPS-only in production (always true via Traefik).
    SameSite=Lax: protects against CSRF while allowing top-level navigations.
    path=/: cookie is sent on every URL under sluiskade.com so the
        future /media/* routes for bewoners also receive it.
    """
    response.set_cookie(
        BEWONER_SESSION_COOKIE,
        _sign_bewoner_session(email),
        max_age=BEWONER_SESSION_MAX_AGE,
        httponly=True,
        secure=not current_app.debug,
        samesite="Lax",
        path="/",
    )
    return response


def clear_bewoner_session_cookie(response):
    response.delete_cookie(BEWONER_SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def require_bewoner_session(view: Callable) -> Callable:
    """Redirect to the login page when no valid bewoner session is present."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not has_valid_bewoner_session():
            return redirect(url_for("portaal_login"))
        return view(*args, **kwargs)
    return wrapper
