"""
Admin authentication: magic-link OTP + wachtwoord (echte 2FA).

Step 1: bewoner_otps tabel hergebruiken voor de OTP-mail. Daarna een
korte 'admin-otp-passed' cookie zodat de wachtwoord-stap weet dat de
email-factor klopte. Stap 2 vraagt het wachtwoord (hash uit env). Pas
als allebei klopt komt er een admin_session cookie van 8 uur.

Waarom geen Authentik? Voor één admin-account is dat een sloopkogel
voor een schroef. Dit dekt:
    - Iets-dat-je-hebt: toegang tot beheer@sluiskade.com mailbox
    - Iets-dat-je-weet: wachtwoord

Het wachtwoord komt nooit langs als plaintext op disk: alleen de hash
(via werkzeug.security.generate_password_hash) zit in .env. De CLI
command `flask gen-admin-password-hash` helpt je een nieuwe te maken.
"""
from __future__ import annotations

import os
from functools import wraps
from typing import Callable, Optional

from flask import current_app, redirect, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash

import bewoner_auth
import db

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "beheer@sluiskade.com").strip().lower()
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()

# Korte cookie tussen OTP-success en wachtwoord-prompt
ADMIN_OTP_COOKIE = "admin_otp_passed"
ADMIN_OTP_COOKIE_MAX_AGE = 60 * 10  # 10 minuten om wachtwoord in te vullen

# Volle admin sessie na succesvol wachtwoord
ADMIN_SESSION_COOKIE = "admin_session"
ADMIN_SESSION_MAX_AGE = 60 * 60 * 8  # 8 uur (korter dan bewoners, admin is gevoeliger)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def is_admin_password_configured() -> bool:
    """True als ADMIN_PASSWORD_HASH een geldige werkzeug-hash bevat.
    Werkzeug-hashes beginnen met methode:salt:hash, bijv. 'pbkdf2:sha256:...'.
    """
    return bool(ADMIN_PASSWORD_HASH) and ":" in ADMIN_PASSWORD_HASH


def is_admin_email(email: str) -> bool:
    """True als de email matched met de geconfigureerde ADMIN_EMAIL.
    Case-insensitive en whitespace-tolerant."""
    if not email:
        return False
    return email.strip().lower() == ADMIN_EMAIL


def verify_admin_password(plain: str) -> bool:
    """Constant-time password check tegen ADMIN_PASSWORD_HASH."""
    if not is_admin_password_configured() or not plain:
        return False
    try:
        return check_password_hash(ADMIN_PASSWORD_HASH, plain)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# OTP wrappers (hergebruik bewoner_otps tabel + bewoner_auth helpers)
# ---------------------------------------------------------------------------

def create_admin_otp() -> str:
    """Maak en bewaar een OTP voor ADMIN_EMAIL. Caller verstuurt 'm via mail."""
    return bewoner_auth.create_and_save_otp(ADMIN_EMAIL)


def verify_admin_otp(code: str) -> bool:
    """Check OTP voor ADMIN_EMAIL. Markeert 'm als gebruikt bij succes."""
    return bewoner_auth.verify_and_consume_otp(ADMIN_EMAIL, code)


# ---------------------------------------------------------------------------
# Cookie signing
# ---------------------------------------------------------------------------

def _otp_serializer() -> URLSafeTimedSerializer:
    """Aparte salt zodat een gelekte bewoner-cookie geen admin-rechten geeft."""
    return URLSafeTimedSerializer(
        secret_key=current_app.config["SECRET_KEY"],
        salt="admin-otp-passed-v1",
    )


def _session_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=current_app.config["SECRET_KEY"],
        salt="admin-session-v1",
    )


def set_otp_passed_cookie(response):
    """Markeert dat de email-factor geslaagd is. Geldig voor de password-stap."""
    response.set_cookie(
        ADMIN_OTP_COOKIE,
        _otp_serializer().dumps({"email": ADMIN_EMAIL}),
        max_age=ADMIN_OTP_COOKIE_MAX_AGE,
        httponly=True,
        secure=not current_app.debug,
        samesite="Lax",
        path="/admin",
    )
    return response


def has_otp_passed() -> bool:
    """True als de huidige request een geldige otp-passed cookie heeft."""
    raw = request.cookies.get(ADMIN_OTP_COOKIE)
    if not raw:
        return False
    try:
        payload = _otp_serializer().loads(raw, max_age=ADMIN_OTP_COOKIE_MAX_AGE)
        return is_admin_email(payload.get("email", ""))
    except (BadSignature, SignatureExpired, Exception):
        return False


def clear_otp_passed_cookie(response):
    response.delete_cookie(ADMIN_OTP_COOKIE, path="/admin")
    return response


def set_admin_session_cookie(response):
    """Volle admin sessie, 8 uur geldig."""
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        _session_serializer().dumps({"email": ADMIN_EMAIL}),
        max_age=ADMIN_SESSION_MAX_AGE,
        httponly=True,
        secure=not current_app.debug,
        samesite="Lax",
        path="/",
    )
    return response


def has_valid_admin_session() -> bool:
    raw = request.cookies.get(ADMIN_SESSION_COOKIE)
    if not raw:
        return False
    try:
        payload = _session_serializer().loads(raw, max_age=ADMIN_SESSION_MAX_AGE)
        return is_admin_email(payload.get("email", ""))
    except (BadSignature, SignatureExpired, Exception):
        return False


def clear_admin_session_cookie(response):
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    response.delete_cookie(ADMIN_OTP_COOKIE, path="/admin")
    return response


def get_admin_email() -> Optional[str]:
    """Email uit de huidige admin-sessie, of None."""
    if has_valid_admin_session():
        return ADMIN_EMAIL
    return None


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def require_admin(view: Callable) -> Callable:
    """Redirect naar /admin/login als er geen geldige admin-sessie is."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not has_valid_admin_session():
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapper
