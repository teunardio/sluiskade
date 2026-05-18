"""
Transactional email via Resend.

Two senders are exposed:

    send_otp_email(to_email, code)
        Magic-link login code for a bewoner.

    send_access_request_notification(req)
        Notify the admin when a new toegangsaanvraag lands.

If RESEND_API_KEY is not set (typical for local dev), the module skips
the network call and prints the email to stdout instead. This means the
login flow keeps working locally without a Resend account: you read the
code from the gunicorn / flask run logs.
"""
from __future__ import annotations

import os
from typing import Optional

import resend

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "Sluiskade <beheer@sluiskade.com>")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "beheer@sluiskade.com")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://sluiskade.com")

if RESEND_API_KEY and RESEND_API_KEY.startswith("re_"):
    resend.api_key = RESEND_API_KEY


def _send(to: str, subject: str, html: str) -> bool:
    """Low-level send. Returns True on success.

    In dev mode (no Resend key) this prints the message and returns True
    so the caller can keep functioning. Never raises on send failure: the
    UX has to keep moving even if the SMTP layer hiccups."""
    if not (RESEND_API_KEY and RESEND_API_KEY.startswith("re_")):
        print("=" * 60)
        print(f"[mail.py DEV] No RESEND_API_KEY, would have sent:")
        print(f"  To:      {to}")
        print(f"  From:    {MAIL_FROM}")
        print(f"  Subject: {subject}")
        print("-" * 60)
        # Print only the body text inside the email card, not the wrapper
        print(html[:2000])
        print("=" * 60)
        return True

    try:
        resend.Emails.send({
            "from": MAIL_FROM,
            "to": to,
            "subject": subject,
            "html": html,
        })
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[mail.py ERROR] Resend send failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

_BASE_STYLE = """
  body { margin: 0; padding: 0; background: #e0f2fe;
         font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         color: #0f172a; }
  .wrap { max-width: 520px; margin: 0 auto; padding: 32px 16px; }
  .card { background: #ffffff; border-radius: 16px; padding: 36px 32px;
          box-shadow: 0 4px 16px rgba(12, 74, 110, 0.08); }
  .brand { font-size: 14px; font-weight: 600; letter-spacing: 0.12em;
           text-transform: uppercase; color: #0ea5e9; margin-bottom: 24px; }
  h1 { font-size: 22px; color: #0c4a6e; margin: 0 0 12px;
       font-weight: 700; line-height: 1.3; }
  p { font-size: 15px; line-height: 1.6; color: #334155; margin: 0 0 16px; }
  .code { display: block; font-family: 'SF Mono', Menlo, Consolas, monospace;
          font-size: 36px; font-weight: 700; letter-spacing: 0.25em;
          color: #0c4a6e; background: #f0f9ff; border: 1px solid #bae6fd;
          border-radius: 12px; padding: 24px; text-align: center;
          margin: 24px 0; }
  .footer { text-align: center; padding: 24px 16px; font-size: 12px;
            color: #64748b; }
  .kvp { font-size: 14px; color: #334155; margin: 6px 0; }
  .kvp b { color: #0c4a6e; }
"""


def send_otp_email(to_email: str, code: str) -> bool:
    subject = f"Je inlogcode voor Sluiskade: {code}"
    html = f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8" />
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="brand">Sluiskade</div>
      <h1>Inloggen op het bewonersportaal</h1>
      <p>Hieronder staat je eenmalige code. Tik die in op de inlogpagina om binnen te komen.</p>
      <div class="code">{code}</div>
      <p>De code blijft 15 minuten geldig en kan maar een keer gebruikt worden. Verstuur 'm niet aan iemand anders.</p>
      <p style="color: #64748b; font-size: 13px;">Heb je deze mail niet aangevraagd? Dan kun je hem rustig weggooien, er gebeurt niets.</p>
    </div>
    <div class="footer">Sluiskade &middot; <a href="{PUBLIC_BASE_URL}" style="color: #0ea5e9;">sluiskade.com</a></div>
  </div>
</body>
</html>"""
    return _send(to_email, subject, html)


def send_admin_otp_email(code: str) -> bool:
    """Stuur de admin OTP-code naar ADMIN_EMAIL.
    Aparte template zodat het duidelijk een admin-mail is, niet bewoner."""
    subject = f"Admin-inlogcode Sluiskade: {code}"
    html = f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8" />
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="brand" style="color: #b45309;">Sluiskade Admin</div>
      <h1>Admin-login (stap 1 van 2)</h1>
      <p>Iemand probeert in te loggen op het admin-paneel. Hieronder de eenmalige code:</p>
      <div class="code">{code}</div>
      <p>Na deze code moet er ook nog een wachtwoord ingevoerd worden. Heb jij dit niet aangevraagd, negeer dan deze mail · zonder het wachtwoord komt niemand binnen.</p>
      <p style="color: #64748b; font-size: 13px;">Code is 15 minuten geldig en kan maar een keer gebruikt worden.</p>
    </div>
    <div class="footer">Sluiskade Admin &middot; <a href="{PUBLIC_BASE_URL}/admin" style="color: #0ea5e9;">sluiskade.com/admin</a></div>
  </div>
</body>
</html>"""
    return _send(ADMIN_EMAIL, subject, html)


def send_access_request_notification(req: dict) -> bool:
    """Notify ADMIN_EMAIL of a new toegangsaanvraag."""
    subject = f"Nieuwe toegangsaanvraag Sluiskade: {req.get('email')}"
    motivatie = req.get("motivatie") or "(geen toelichting)"
    html = f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8" />
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="brand">Sluiskade Admin</div>
      <h1>Nieuwe toegangsaanvraag</h1>
      <p>Iemand vraagt toegang tot het bewonersportaal:</p>

      <p class="kvp"><b>Naam:</b> {req.get('voornaam', '')} {req.get('achternaam', '')}</p>
      <p class="kvp"><b>E-mail:</b> {req.get('email', '')}</p>
      <p class="kvp"><b>Motivatie:</b> {motivatie}</p>
      <p class="kvp"><b>Aangevraagd:</b> {req.get('requested_at', '')}</p>

      <p style="margin-top: 24px;">Goedkeuren? Voeg toe vanaf de Coolify container terminal:</p>
      <pre style="background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 8px;
                  padding: 12px; font-size: 13px; overflow-x: auto;">flask add-bewoner --email={req.get('email', '')} --name="{req.get('voornaam', '')} {req.get('achternaam', '')}"</pre>
      <p style="color: #64748b; font-size: 13px;">De admin UI met one-click goedkeuren komt in Sprint 3.</p>
    </div>
    <div class="footer">Sluiskade Admin notificatie</div>
  </div>
</body>
</html>"""
    return _send(ADMIN_EMAIL, subject, html)
