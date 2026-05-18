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
import bewoner_auth
import mail
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

# Initialize the database on import (idempotent · safe under gunicorn forks)
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


@app.route("/portaal")
def portaal_index():
    """Bewoners landing: login if no session, otherwise straight to gallery."""
    if not bewoner_auth.has_valid_bewoner_session():
        return redirect(url_for("portaal_login"))
    return redirect(url_for("portaal_gallery"))


@app.route("/portaal/gallery")
@bewoner_auth.require_bewoner_session
def portaal_gallery():
    """Show all photos for logged-in bewoners (newest first)."""
    photos = db.list_photos(limit=200)
    return render_template(
        "portaal/gallery.html",
        photos=photos,
        total_photos=db.count_photos(),
        email=bewoner_auth.get_current_bewoner_email(),
    )


@app.route("/portaal/upload", methods=["GET", "POST"])
@bewoner_auth.require_bewoner_session
def portaal_upload():
    """Bewoners can also upload photos (their own from a walk past the site)."""
    email = bewoner_auth.get_current_bewoner_email()

    if request.method == "POST":
        return _handle_bewoner_upload(email)

    return render_template(
        "portaal/upload.html",
        email=email,
        total_photos=db.count_photos(),
        error=request.args.get("error"),
    )


def _handle_bewoner_upload(email: str):
    """Same pipeline as sluiswachter upload, tagged with source=bewoner."""
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        return redirect(url_for("portaal_upload", error="Geen foto's geselecteerd."))

    saved_ids: list[int] = []
    errors: list[tuple[str, str]] = []

    for f in files:
        try:
            result = save_photo(f)
            photo_id = db.insert_photo(
                filename=result.filename,
                thumb_filename=result.thumb_filename,
                source="bewoner",
                uploader_email=email,
                width=result.width,
                height=result.height,
                file_size=result.file_size,
            )
            saved_ids.append(photo_id)
        except PhotoError as exc:
            errors.append((f.filename, str(exc)))
            app.logger.warning("Bewoner upload failed for %s: %s", f.filename, exc)
        except Exception:  # noqa: BLE001
            errors.append((f.filename, "Onverwachte fout, de beheerder kijkt ernaar."))
            app.logger.exception("Unexpected bewoner upload error for %s", f.filename)

    if not saved_ids:
        first_error = errors[0][1] if errors else "Onbekende fout."
        return redirect(url_for("portaal_upload", error=first_error))

    # Land back on the gallery so they see their photo at the top
    return redirect(url_for("portaal_gallery"))


@app.route("/portaal/foto/<int:photo_id>")
@bewoner_auth.require_bewoner_session
def portaal_view_photo(photo_id: int):
    """Full-screen single photo view for bewoners."""
    photo = db.get_photo(photo_id)
    if not photo or photo.get("deleted_at"):
        abort(404)
    return render_template("portaal/foto.html", photo=photo)


@app.route("/portaal/aanvragen", methods=["GET", "POST"])
def portaal_aanvragen():
    """Public access-request form. No auth needed."""
    if request.method == "POST":
        voornaam = request.form.get("voornaam", "").strip()
        achternaam = request.form.get("achternaam", "").strip()
        email = request.form.get("email", "").strip().lower()
        motivatie = request.form.get("motivatie", "").strip() or None

        # Basic validation
        if not voornaam or not achternaam or not email or "@" not in email:
            return render_template(
                "portaal/aanvragen.html",
                error="Vul je voornaam, achternaam en een geldig e-mailadres in.",
                voornaam=voornaam,
                achternaam=achternaam,
                email=email,
                motivatie=motivatie,
            )

        # If they're already on the whitelist, skip the request and
        # just nudge them to the login page.
        if db.is_email_allowed(email):
            return render_template(
                "portaal/aanvragen.html",
                error="Je hebt al toegang. Ga naar inloggen en vul je e-mailadres in.",
                voornaam=voornaam,
                achternaam=achternaam,
                email=email,
                motivatie=motivatie,
                already_allowed=True,
            )

        request_id = db.save_access_request(email, voornaam, achternaam, motivatie)
        req = db.get_access_request(request_id)
        mail.send_access_request_notification(req)
        app.logger.info("New access request from %s", email)

        return redirect(url_for("portaal_aanvragen_bedankt"))

    return render_template("portaal/aanvragen.html")


@app.route("/portaal/aanvragen/bedankt")
def portaal_aanvragen_bedankt():
    """Confirmation page after submitting an access request."""
    return render_template("portaal/aanvragen_bedankt.html")


@app.route("/portaal/login", methods=["GET", "POST"])
def portaal_login():
    """Step 1 of the magic-link flow: bewoner submits their email."""
    if bewoner_auth.has_valid_bewoner_session() and request.method == "GET":
        return redirect(url_for("portaal_index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email or "@" not in email:
            return render_template(
                "portaal/login.html",
                error="Vul een geldig e-mailadres in.",
                email=email,
            )

        if not db.is_email_allowed(email):
            # Do not confirm whether the email exists, just hint at the
            # access-request flow. The aanvragen route lands in the next step.
            return render_template(
                "portaal/login.html",
                error="Dit e-mailadres staat (nog) niet op de lijst.",
                email=email,
                show_request_link=True,
            )

        code = bewoner_auth.create_and_save_otp(email)
        mail.send_otp_email(email, code)
        app.logger.info("Sent OTP to %s", email)

        return redirect(url_for("portaal_verify", email=email))

    return render_template("portaal/login.html")


@app.route("/portaal/verify", methods=["GET", "POST"])
def portaal_verify():
    """Step 2 of the magic-link flow: bewoner types the OTP we mailed."""
    email = (
        request.form.get("email", "")
        or request.args.get("email", "")
    ).strip().lower()

    if not email:
        return redirect(url_for("portaal_login"))

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if not bewoner_auth.verify_and_consume_otp(email, code):
            return render_template(
                "portaal/verify.html",
                email=email,
                error="De code klopt niet, of is verlopen. Probeer opnieuw of vraag een nieuwe code aan.",
            )

        response = make_response(redirect(url_for("portaal_index")))
        return bewoner_auth.set_bewoner_session_cookie(response, email)

    return render_template("portaal/verify.html", email=email)


@app.route("/portaal/logout", methods=["POST"])
def portaal_logout():
    response = make_response(redirect(url_for("portaal_login")))
    return bewoner_auth.clear_bewoner_session_cookie(response)


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
        # Token strips out of the URL via redirect · keeps it out of browser history
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
            errors.append((f.filename, "Onverwachte fout · de beheerder kijkt ernaar."))
            app.logger.exception("Unexpected upload error for %s", f.filename)

    if not saved_ids:
        first_error = errors[0][1] if errors else "Onbekende fout."
        return redirect(url_for("sluis_upload", error=first_error))

    ok_param = ",".join(str(i) for i in saved_ids[:10])  # cap for URL length
    target = url_for("sluis_bedankt", ok=ok_param)
    if errors:
        target += f"&fail={len(errors)}"
    return redirect(target)


@app.route("/sluis/gallery")
@require_sluis_session
def sluis_gallery():
    """Show every uploaded photo (newest first) in a grid."""
    photos = db.list_photos(limit=200)
    return render_template(
        "sluis/gallery.html",
        photos=photos,
        total_photos=db.count_photos(),
    )


@app.route("/sluis/foto/<int:photo_id>")
@require_sluis_session
def sluis_view_photo(photo_id: int):
    """Full-screen preview of one photo."""
    photo = db.get_photo(photo_id)
    if not photo or photo.get("deleted_at"):
        abort(404)
    return render_template("sluis/foto.html", photo=photo)


@app.route("/sluis/foto/<int:photo_id>/delete", methods=["POST"])
@require_sluis_session
def sluis_delete_photo(photo_id: int):
    """
    Soft-delete a photo. Files stay on disk; the row gets deleted_at set
    and disappears from every public view. Admin trash recovery comes in
    Sprint 3.

    Responds with JSON for AJAX callers, redirects for plain form posts.
    """
    deleted = db.soft_delete_photo(photo_id, deleted_by="sluis")
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if is_ajax:
        return {"ok": deleted, "id": photo_id}, (200 if deleted else 404)

    return redirect(url_for("sluis_gallery"))


@app.route("/sluis/bedankt")
@require_sluis_session
def sluis_bedankt():
    """Confirmation page · shows the thumbnails of what just landed."""
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

def _has_any_user_session() -> bool:
    """Either a sluiswachter QR session or a bewoner login grants media access."""
    return has_valid_sluis_session() or bewoner_auth.has_valid_bewoner_session()


@app.route("/media/thumbs/<int:photo_id>")
def serve_thumb(photo_id: int):
    """Serve a thumbnail. Allowed for sluis OR bewoner sessions."""
    if not _has_any_user_session():
        abort(403)
    photo = db.get_photo(photo_id)
    if not photo or photo.get("deleted_at") or not photo.get("thumb_filename"):
        abort(404)
    return send_from_directory(
        photo_service.THUMBS_DIR,
        photo["thumb_filename"],
        max_age=3600,
    )


@app.route("/media/photos/<int:photo_id>")
def serve_photo(photo_id: int):
    """Serve a full-size photo. Allowed for sluis OR bewoner sessions."""
    if not _has_any_user_session():
        abort(403)
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

@app.cli.command("add-bewoner")
@click.option("--email", required=True, help="Email address to whitelist.")
@click.option("--name", default="", help="Display name (optional).")
def add_bewoner(email: str, name: str):
    """Add an email to the bewoners whitelist.

    Usage from the Coolify container terminal:
        flask add-bewoner --email=teunard@example.com --name="Teunard"
    """
    db.add_allowed_resident(email.lower(), name=(name or None), added_by="cli")
    click.echo(f"  Whitelisted: {email}")


@app.cli.command("list-bewoners")
def list_bewoners():
    """List every email on the bewoners whitelist."""
    rows = db.list_allowed_residents()
    if not rows:
        click.echo("(no bewoners whitelisted yet)")
        return
    for r in rows:
        click.echo(f"  {r['email']:<40} {r.get('name') or ''}  (added {r['added_at']})")


@app.cli.command("remove-bewoner")
@click.option("--email", required=True)
def remove_bewoner(email: str):
    """Remove an email from the bewoners whitelist."""
    if db.remove_allowed_resident(email.lower()):
        click.echo(f"  Removed: {email}")
    else:
        click.echo(f"  Not found: {email}")


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
