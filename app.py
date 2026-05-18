"""
Sluiskade — Flask app entry point.

Currently live routes:
    GET  /            Public placeholder splash
    GET  /healthz     Container healthcheck
    GET  /sluis       Sluiswachter entry point (validates QR token, sets
                      shift cookie, shows welcome page)

CLI commands:
    flask gen-qr-token        Generate a fresh QR token + URL for printing
"""
import os
import click
from flask import (
    Flask,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from tokens import (
    generate_qr_token,
    has_valid_sluis_session,
    set_sluis_session_cookie,
    verify_qr_token,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "dev-secret-do-not-use-in-production",
)

# Public base URL used when printing a QR token. Override in Coolify env
# vars if the domain ever changes.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://sluiskade.com")


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Public splash — the community one-pager replaces this in Sprint 2."""
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    """Healthcheck — used by Docker HEALTHCHECK + Uptime Kuma."""
    return {"status": "ok"}, 200


# ---------------------------------------------------------------------------
# Sluiswachter entry point
# ---------------------------------------------------------------------------

@app.route("/sluis")
def sluis_entry():
    """
    Single entry point for sluiswachters.

    Three paths through this route:

    1.  Fresh QR scan: ?t=<token> is present and valid
        → set shift cookie, redirect to /sluis (no token in URL anymore)

    2.  Returning sluiswachter within their shift: valid cookie present
        → show the welcome page

    3.  Anyone else (no token, expired cookie, bad token):
        → 403 "geen toegang"
    """
    incoming_token = request.args.get("t")

    if incoming_token and verify_qr_token(incoming_token):
        # Valid scan — set the shift cookie and redirect to a clean URL.
        # Redirecting strips the token from the address bar so it won't
        # end up in browser history / screenshots / shoulder-surfing.
        response = make_response(redirect(url_for("sluis_entry")))
        return set_sluis_session_cookie(response)

    if has_valid_sluis_session():
        return render_template("sluis/welkom.html")

    abort(403)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(_err):
    return render_template("errors/geen_toegang.html"), 403


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@app.cli.command("gen-qr-token")
def gen_qr_token():
    """Generate a fresh QR token and print the full URL.

    Usage in production (from Coolify container terminal):
        flask gen-qr-token
    """
    token = generate_qr_token()
    url = f"{PUBLIC_BASE_URL}/sluis?t={token}"
    click.echo("")
    click.echo("Nieuwe QR-token gegenereerd:")
    click.echo("")
    click.echo(f"  {url}")
    click.echo("")
    click.echo("Plak deze URL in een QR-generator (bijv. qr.io, of straks de")
    click.echo("ingebouwde admin-pagina) en print 'm op A4 voor in de sluis.")
    click.echo("")


if __name__ == "__main__":
    # Local dev only; production runs under gunicorn (see Dockerfile CMD)
    app.run(host="0.0.0.0", port=5000, debug=True)
