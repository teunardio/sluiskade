"""
Token signing & verification for the sluiswachter flow.

Two distinct signed tokens are used:

1.  **QR token** — long-lived, embedded in the QR code printed on A4.
    Anyone scanning the QR is granted access. Signed with QR_TOKEN_SECRET
    and salted with the current QR_TOKEN_VERSION, so bumping the version
    env var instantly invalidates every existing QR code without touching
    the database.

2.  **Sluis session cookie** — short-lived (12 hours), set after a valid
    QR scan. Lets the sluiswachter upload multiple photos without
    re-scanning during their shift. Signed with SECRET_KEY and salted
    independently so leaking one secret doesn't compromise the other.

Both use itsdangerous.URLSafeTimedSerializer — battle-tested, URL-safe,
includes timestamp so we can enforce max-age.
"""
from __future__ import annotations

import os
from functools import wraps
from typing import Callable

from flask import abort, current_app, make_response, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# ---------------------------------------------------------------------------
# Configuration (read from env at module load; falls back to dev defaults so
# the app can still boot locally without a .env file present)
# ---------------------------------------------------------------------------

QR_TOKEN_SECRET = os.environ.get(
    "QR_TOKEN_SECRET",
    "dev-qr-secret-do-not-use-in-production",
)
QR_TOKEN_VERSION = int(os.environ.get("QR_TOKEN_VERSION", "1"))

# QR codes are designed to live on a wall for months. We set a hard ceiling
# of 5 years; rotation is done by bumping QR_TOKEN_VERSION, not by expiry.
QR_TOKEN_MAX_AGE = 60 * 60 * 24 * 365 * 5  # 5 years

# Shift cookie lifetime — a sluiswachter doesn't sit longer than this
SLUIS_SESSION_MAX_AGE = 60 * 60 * 12  # 12 hours
SLUIS_SESSION_COOKIE = "sluis_session"


# ---------------------------------------------------------------------------
# QR token — signed payload embedded in the QR code on A4
# ---------------------------------------------------------------------------

def _qr_serializer() -> URLSafeTimedSerializer:
    """Return a serializer salted with the current QR token version.

    Bumping QR_TOKEN_VERSION changes the salt, which means all tokens
    signed under the previous version fail verification immediately.
    """
    return URLSafeTimedSerializer(
        secret_key=QR_TOKEN_SECRET,
        salt=f"sluis-qr-v{QR_TOKEN_VERSION}",
    )


def generate_qr_token() -> str:
    """Generate a fresh QR token for a new printed QR code."""
    return _qr_serializer().dumps({"role": "sluis", "v": QR_TOKEN_VERSION})


def verify_qr_token(token: str) -> bool:
    """Return True if the token is a valid, non-expired QR token.

    Any failure (bad signature, expired, wrong version) returns False —
    we never want to leak why validation failed.
    """
    try:
        payload = _qr_serializer().loads(token, max_age=QR_TOKEN_MAX_AGE)
        return payload.get("role") == "sluis"
    except (BadSignature, SignatureExpired, Exception):
        return False


# ---------------------------------------------------------------------------
# Sluis session cookie — short-lived shift cookie set after a valid scan
# ---------------------------------------------------------------------------

def _session_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=current_app.config["SECRET_KEY"],
        salt="sluis-session-v1",
    )


def _sign_sluis_session() -> str:
    return _session_serializer().dumps({"role": "sluis"})


def has_valid_sluis_session() -> bool:
    """Check the incoming request for a valid sluis_session cookie."""
    raw = request.cookies.get(SLUIS_SESSION_COOKIE)
    if not raw:
        return False
    try:
        payload = _session_serializer().loads(raw, max_age=SLUIS_SESSION_MAX_AGE)
        return payload.get("role") == "sluis"
    except (BadSignature, SignatureExpired, Exception):
        return False


def set_sluis_session_cookie(response):
    """Attach a freshly-signed sluis session cookie to the response.

    HttpOnly: not readable from JavaScript (XSS defense).
    Secure: only sent over HTTPS (always true in production via Traefik).
    SameSite=Lax: protects against CSRF while allowing the QR-scan redirect.
    path=/: cookie is sent for every URL under the domain — needed so
        the auth'd /media/* routes also receive it. The cookie name is
        unique per role (sluis_session, bewoner_session, etc.), so paths
        don't have to enforce isolation.
    """
    # Wipe any older path-scoped cookie from a previous deploy. Cookies
    # with different paths coexist; without this, both old and new would
    # be sent and the wrong one could win. Safe to call even when no old
    # cookie exists. Can be removed once all live sessions have rotated.
    response.delete_cookie(SLUIS_SESSION_COOKIE, path="/sluis")

    response.set_cookie(
        SLUIS_SESSION_COOKIE,
        _sign_sluis_session(),
        max_age=SLUIS_SESSION_MAX_AGE,
        httponly=True,
        secure=not current_app.debug,  # allow HTTP cookies during local dev
        samesite="Lax",
        path="/",
    )
    return response


def clear_sluis_session_cookie(response):
    response.delete_cookie(SLUIS_SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------------------
# Decorator — protect every /sluis/* route except the entry point itself
# ---------------------------------------------------------------------------

def require_sluis_session(view: Callable) -> Callable:
    """Refuse access unless the request carries a valid sluis_session cookie."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not has_valid_sluis_session():
            abort(403)
        return view(*args, **kwargs)
    return wrapper
