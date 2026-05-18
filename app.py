"""
Sluiskade - Flask app entry point.

Currently live routes:
    GET  /                      Public placeholder splash
    GET  /healthz               Container healthcheck
    GET  /sluis                 Sluiswachter QR-entry (validates token, sets cookie)
    GET  /sluis/upload          Upload form (cookie required)
    POST /sluis/upload          Process one or more uploads
    GET  /sluis/bedankt         Thank-you page with thumbnail previews
    GET  /media/thumbs/<id>     Serve thumbnail (cookie required)
    GET  /media/photos/<id>     Serve full-size photo (cookie required)

CLI commands:
    flask gen-qr-token          Generate a fresh QR token + URL for printing
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
    send_from_directory,
    url_for,
)

import db
import photo_service
from photo_service import (
    PhotoError,
    save_photo,
)
from tokens import (
    generate_qr_token,
    has_valid_sluis_session,
    require_sluis_session,
    set_sluis_session_cookie,
    verify_qr_token,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "dev-secret-do-not-use-in-production",
)

# Total request body cap. Per-file size is enforced separately in
# photo_service. 150 MB allows a comfortable batch upload of ~6 photos.
app.config["MAX_CONTENT_LENGTH"] = 150 * 1024 * 1024

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://sluiskade.com")
GOAL_TOTAL = int(os.environ.get("GOAL_TOTAL", "1000"))

# Initialize the database on import (idempotent — safe under gunicorn forks)
db.init_db()


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Public splash - the community one-pager replaces this in Sprint 2."""
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    """Healthcheck - used by Docker HEALTHCHECK + Uptime Kuma."""
    return {"status": "ok"}, 200


# ---------------------------------------------------------------------------
# Sluiswachter flow
# ---------------------------------------------------------------------------

@app.route("/sluis")
def sluis_entry():
    """
    Single entry point for sluiswachters.

    - Fresh scan (?t=<token> present and valid): set cookie, redirect to upload
    - Returning shift (valid cookie): redirect to upload
    - Anyone else: 403 "geen toegang"
    """
    incoming_token = request.args.get("t")

    if incoming_token and verify_qr_token(incoming_token):
        # Token strips out of the URL via redirect — keeps it out of browser history
        response = make_response(redirect(url_for("sluis_upload")))
        return set_sluis_session_cookie(response)

    if has_valid_sluis_session():
        return redirect(url_for("sluis_upload"))

    abort(403)


@app.route("/sluis/upload", methods=["GET", "POST"])
@require_sluis_session
def sluis_upload():
    """Show the upload form (GET) or process one or more uploads (POST)."""
    if request.method == "POST":
        return _handle_upload()

    return render_template(
        "sluis/upload.html",
        total_photos=db.count_photos(),
        goal_total=GOAL_TOTAL,
        error=request.args.get("error"),
    )


def _handle_upload():
    """Process a multi-file POST. Saves what it can; reports the rest."""
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        return redirect(url_for("sluis_upload", error="Geen foto's geselecteerd."))

    saved_ids: list[int] = []
    errors: list[tuple[str, str]] = []

    for f in files:
        try:
            result = save_photo(f)
            photo_id = db.insert_photo(
                filename=result.filename,
                thumb_filename=result.thumb_filename,
                source="sluis",
                width=result.width,
                height=result.height,
                file_size=result.file_size,
            )
            saved_ids.append(photo_id)
        except PhotoError as exc:
            errors.append((f.filename, str(exc)))
            app.logger.warning("Upload failed for %s: %s", f.filename, exc)
        except Exception as exc:  # noqa: BLE001 - last-resort catch
            errors.append((f.filename, "Onverwachte fout — Teunard kijkt ernaar."))
            app.logger.exception("Unexpected upload error for %s", f.filename)

    if not saved_ids:
        first_error = errors[0][1] if errors else "Onbekende fout."
        return redirect(url_for("sluis_upload", error=first_error))

    ok_param = ",".join(str(i) for i in saved_ids[:10])  # cap for URL length
    target = url_for("sluis_bedankt", ok=ok_param)
    if errors:
        target += f"&fail={len(errors)}"
    return redirect(target)


@app.route("/sluis/bedankt")
@require_sluis_session
def sluis_bedankt():
    """Confirmation page — shows the thumbnails of what just landed."""
    ok_str = request.args.get("ok", "")
    photo_ids = [int(x) for x in ok_str.split(",") if x.isdigit()][:10]

    if not photo_ids:
        return redirect(url_for("sluis_upload"))

    try:
        fail_count = int(request.args.get("fail", "0"))
    except ValueError:
        fail_count = 0

    total = db.count_photos()

    return render_template(
        "sluis/bedankt.html",
        photo_ids=photo_ids,
        fail_count=fail_count,
        total_photos=total,
        goal_total=GOAL_TOTAL,
        remaining=max(0, GOAL_TOTAL - total),
    )


# ---------------------------------------------------------------------------
# Media serving (auth-checked)
# ---------------------------------------------------------------------------

@app.route("/media/thumbs/<int:photo_id>")
@require_sluis_session
def serve_thumb(photo_id: int):
    """Serve a thumbnail. Widens to bewoner-session in Sprint 2."""
    photo = db.get_photo(photo_id)
    if not photo or photo.get("deleted_at") or not photo.get("thumb_filename"):
        abort(404)
    return send_from_directory(
        photo_service.THUMBS_DIR,
        photo["thumb_filename"],
        max_age=3600,
    )


@app.route("/media/photos/<int:photo_id>")
@require_sluis_session
def serve_photo(photo_id: int):
    """Serve a full-size photo. Widens to bewoner-session in Sprint 2."""
    photo = db.get_photo(photo_id)
    if not photo or photo.get("deleted_at"):
        abort(404)
    return send_from_directory(
        photo_service.PHOTOS_DIR,
        photo["filename"],
        max_age=3600,
    )


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(_err):
    return render_template("errors/geen_toegang.html"), 403


@app.errorhandler(413)
def too_large(_err):
    """Triggered when the total request body exceeds MAX_CONTENT_LENGTH."""
    return redirect(url_for(
        "sluis_upload",
        error="De foto's samen zijn te groot. Probeer er minder tegelijk.",
    ))


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
