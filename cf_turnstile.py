"""
Cloudflare Turnstile server-side verifier.

Turnstile is Cloudflare's gratis CAPTCHA-alternatief: zichtbaar als een
klein widget, maar in 9 van de 10 gevallen non-interactief (gewoon een
groene check). Privacy-vriendelijk, geen tracking-cookies, geen externe
JavaScript-vendor lock-in.

Flow:
    1. Pagina laadt /turnstile/v0/api.js en rendert <div class="cf-turnstile">
    2. Cloudflare schiet een token terug in het form-veld `cf-turnstile-response`
    3. Bij submit POST'en wij die token naar siteverify met onze SECRET
    4. Cloudflare zegt success: true/false, wij accepteren of weigeren

Configuratie via .env:
    CF_TURNSTILE_SITEKEY=0x...      # public, mag in HTML
    CF_TURNSTILE_SECRET=0x...       # geheim, alleen op de server

Niet ingesteld = fail-open (handig voor lokale dev en als Cloudflare-
account nog opgezet moet worden). Wel ingesteld = elke aanvraag moet
een geldige token meesturen.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

CF_TURNSTILE_SITEKEY = os.environ.get("CF_TURNSTILE_SITEKEY", "").strip()
CF_TURNSTILE_SECRET = os.environ.get("CF_TURNSTILE_SECRET", "").strip()

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
VERIFY_TIMEOUT_SECONDS = 5


def is_configured() -> bool:
    """True als zowel sitekey als secret in de env staan."""
    return bool(CF_TURNSTILE_SITEKEY and CF_TURNSTILE_SECRET)


def verify_token(token: str, *, remote_ip: Optional[str] = None) -> bool:
    """Valideer een Turnstile-token bij Cloudflare.

    Returnt True als:
        - Turnstile niet geconfigureerd is (fail-open voor dev)
        - Cloudflare antwoordt met success: true

    Returnt False als:
        - Token is leeg of duidelijk ongeldig
        - Cloudflare antwoordt met success: false

    Bij netwerk-fouten (timeout, DNS, etc) loggen we naar stdout en
    returnen True om gebruikers niet buiten te sluiten bij infra-issues.
    Trade-off: liever een paar bot-aanvragen door dan échte bewoners
    afwijzen omdat Cloudflare één seconde hikt.
    """
    if not is_configured():
        return True
    if not token or not isinstance(token, str):
        return False

    payload = {
        "secret": CF_TURNSTILE_SECRET,
        "response": token,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        VERIFY_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req, timeout=VERIFY_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
            return bool(result.get("success"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
        print(f"[cf_turnstile] siteverify mislukt, fail-open: {exc}")
        return True
