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
import io
import os
import zipfile

import click
from flask import (
    Flask,
    Response,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import admin_auth
import bewoner_auth
import cf_turnstile
import db
import mail
import photo_service
from werkzeug.security import generate_password_hash
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

@app.context_processor
def inject_session_flags():
    """Maak is_admin overal in templates beschikbaar zonder dat elke route
    'm expliciet hoeft door te geven. Templates die de portaal-topbar
    aanroepen kunnen direct {{ is_admin }} gebruiken."""
    return {
        "is_admin": admin_auth.has_valid_admin_session(),
    }


# Initialize the database on import (idempotent · safe under gunicorn forks)
db.init_db()

# Start de auto-purge scheduler. Filesystem-lock zorgt ervoor dat alleen
# één Gunicorn-worker de jobs daadwerkelijk draait.
import scheduler  # noqa: E402
scheduler.init_scheduler()


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


@app.route("/sw.js")
def service_worker():
    """Server de service worker vanuit de root zodat hij scope=/ heeft.
    Vanuit /static/sw.js zou de scope alleen /static/ zijn, daar hebben we
    niks aan. Cache-Control op no-store omdat SW-updates meteen actief
    moeten worden bij een deploy."""
    response = make_response(send_from_directory(
        app.static_folder, "sw.js", mimetype="application/javascript",
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/portaal")
def portaal_index():
    """Bewoners dashboard. Stats + latest photos + quick-access cards."""
    if not bewoner_auth.has_valid_bewoner_session():
        return redirect(url_for("portaal_login"))

    email = bewoner_auth.get_current_bewoner_email()
    stats = db.photo_stats()
    own_count = db.count_photos_by_uploader(email)
    latest = db.list_photos(limit=4)
    return render_template(
        "portaal/dashboard.html",
        email=email,
        stats=stats,
        own_count=own_count,
        latest=latest,
    )


@app.route("/portaal/gallery")
@bewoner_auth.require_bewoner_session
def portaal_gallery():
    """Show all photos for logged-in bewoners (newest first)."""
    email = bewoner_auth.get_current_bewoner_email()
    photos = db.list_photos_with_likes(limit=200, viewer_email=email)
    return render_template(
        "portaal/gallery.html",
        photos=photos,
        total_photos=db.count_photos(),
        email=email,
    )


@app.route("/portaal/tijdlijn")
@bewoner_auth.require_bewoner_session
def portaal_tijdlijn():
    """Photos grouped by date with human labels (Vandaag, Gisteren, etc)."""
    email = bewoner_auth.get_current_bewoner_email()
    photos = db.list_photos_with_likes(limit=500, viewer_email=email)
    groups = _group_photos_by_date(photos)
    return render_template(
        "portaal/tijdlijn.html",
        groups=groups,
        total_photos=db.count_photos(),
        email=email,
    )


@app.route("/portaal/timelapse")
@bewoner_auth.require_bewoner_session
def portaal_timelapse():
    """Autoplay slideshow through all photos in chronological order
    (oldest to newest, so the build grows in front of you)."""
    email = bewoner_auth.get_current_bewoner_email()
    photos = db.list_photos_for_timeline(limit=500)
    # Reverse to oldest-first for the timelapse to show progression
    photos = list(reversed(photos))
    return render_template(
        "portaal/timelapse.html",
        photos=photos,
        total=len(photos),
        email=email,
    )


@app.route("/portaal/random")
@bewoner_auth.require_bewoner_session
def portaal_random():
    """Verras me: pick a random photo and bounce to its view."""
    photo_id = db.random_visible_photo_id()
    if photo_id is None:
        return redirect(url_for("portaal_gallery"))
    return redirect(url_for("portaal_view_photo", photo_id=photo_id))


def _group_photos_by_date(photos: list[dict]) -> list[dict]:
    """Group photos into labelled buckets for the timeline view.
    Returns list of {label, photos} dicts in display order."""
    from datetime import datetime, date, timedelta

    today = date.today()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=7)

    buckets: dict[str, list] = {}
    order: list[str] = []
    month_names = {
        1: "Januari", 2: "Februari", 3: "Maart", 4: "April",
        5: "Mei", 6: "Juni", 7: "Juli", 8: "Augustus",
        9: "September", 10: "Oktober", 11: "November", 12: "December",
    }

    for p in photos:
        try:
            d = datetime.strptime(p["uploaded_at"][:10], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue

        if d == today:
            label = "Vandaag"
        elif d == yesterday:
            label = "Gisteren"
        elif d > week_start:
            label = "Deze week"
        else:
            label = f"{month_names[d.month]} {d.year}"

        if label not in buckets:
            buckets[label] = []
            order.append(label)
        buckets[label].append(p)

    return [{"label": lbl, "photos": buckets[lbl]} for lbl in order]


@app.route("/portaal/foto/<int:photo_id>/like", methods=["POST"])
@bewoner_auth.require_bewoner_session
def portaal_toggle_like(photo_id: int):
    """Toggle like on a photo. Returns the new state for AJAX callers."""
    photo = db.get_photo(photo_id)
    if not photo or photo.get("deleted_at"):
        abort(404)
    email = bewoner_auth.get_current_bewoner_email()
    liked = db.toggle_photo_like(photo_id, email)
    count = db.count_photo_likes(photo_id)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"liked": liked, "count": count}, 200
    return redirect(request.referrer or url_for("portaal_view_photo", photo_id=photo_id))


@app.route("/portaal/download/photo/<int:photo_id>")
@bewoner_auth.require_bewoner_session
def portaal_download_photo(photo_id: int):
    """Single-photo download with a friendly filename."""
    photo = db.get_photo(photo_id)
    if not photo or photo.get("deleted_at"):
        abort(404)
    pretty_date = photo["uploaded_at"][:10]
    return send_from_directory(
        photo_service.PHOTOS_DIR,
        photo["filename"],
        as_attachment=True,
        download_name=f"sluiskade-{pretty_date}-{photo_id}.jpg",
    )


@app.route("/portaal/download/all.zip")
@bewoner_auth.require_bewoner_session
def portaal_download_all():
    """Build a ZIP of every visible photo in memory and return it.

    Uses ZIP_STORED (no compression) because JPEGs do not recompress
    meaningfully and STORED is much faster. Works comfortably up to a
    few hundred photos. If we ever cross 1000+, switch to a streaming
    library like zipstream-ng.
    """
    photos = db.list_photos(limit=2000)
    if not photos:
        return redirect(url_for("portaal_gallery"))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for p in photos:
            path = os.path.join(photo_service.PHOTOS_DIR, p["filename"])
            if not os.path.isfile(path):
                continue
            arcname = f"sluiskade-{p['uploaded_at'][:10]}-{p['id']}.jpg"
            zf.write(path, arcname=arcname)

    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="sluiskade-archief.zip"',
            "Cache-Control": "no-store",
        },
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
        max_files=photo_service.MAX_FILES_PER_UPLOAD,
        max_file_bytes=photo_service.MAX_UPLOAD_BYTES,
        max_total_bytes=app.config["MAX_CONTENT_LENGTH"],
    )


def _handle_bewoner_upload(email: str):
    """Same pipeline as sluiswachter upload, tagged with source=bewoner.
    A single optional caption applies to every file in the batch.
    Returnt JSON voor AJAX-calls, redirect voor plain form posts."""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    files = [f for f in request.files.getlist("photos") if f and f.filename]

    if not files:
        return _upload_error_response(
            "Geen foto's geselecteerd.", is_ajax,
            redirect_url=url_for("portaal_upload"),
        )

    if len(files) > photo_service.MAX_FILES_PER_UPLOAD:
        return _upload_error_response(
            f"Maximaal {photo_service.MAX_FILES_PER_UPLOAD} foto's tegelijk. "
            f"Je probeerde er {len(files)} te uploaden, splits het op in batches.",
            is_ajax,
            redirect_url=url_for("portaal_upload"),
        )

    caption_raw = (request.form.get("caption") or "").strip()
    caption = caption_raw[:240] if caption_raw else None

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
                caption=caption,
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
        return _upload_error_response(
            first_error, is_ajax, redirect_url=url_for("portaal_upload"),
        )

    if is_ajax:
        return {
            "ok": True,
            "saved": len(saved_ids),
            "failed": len(errors),
            "errors": [{"file": fn, "msg": msg} for fn, msg in errors],
            "redirect": url_for("portaal_gallery"),
        }, 200
    return redirect(url_for("portaal_gallery"))


def _upload_error_response(message: str, is_ajax: bool, *, redirect_url: str):
    """Geef de juiste error-response: JSON voor XHR, redirect voor form."""
    if is_ajax:
        return {"ok": False, "error": message}, 400
    return redirect(f"{redirect_url}?error={message}")


def _is_bewoner_own_photo(photo: dict, email: str) -> bool:
    """True if this photo was uploaded by the given bewoner."""
    if not email:
        return False
    return (
        photo.get("source") == "bewoner"
        and (photo.get("uploader_email") or "").lower() == email.lower()
    )


@app.route("/portaal/foto/<int:photo_id>")
@bewoner_auth.require_bewoner_session
def portaal_view_photo(photo_id: int):
    """Full-screen single photo view for bewoners."""
    photo = db.get_photo(photo_id)
    if not photo or photo.get("deleted_at"):
        abort(404)
    email = bewoner_auth.get_current_bewoner_email()
    photo["like_count"] = db.count_photo_likes(photo_id)
    photo["liked_by_me"] = db.is_photo_liked_by(photo_id, email)
    return render_template(
        "portaal/foto.html",
        photo=photo,
        is_mine=_is_bewoner_own_photo(photo, email),
    )


@app.route("/portaal/foto/<int:photo_id>/delete", methods=["POST"])
@bewoner_auth.require_bewoner_session
def portaal_delete_photo(photo_id: int):
    """Verwijder een foto. Twee paden:

        - **Bewoner verwijdert eigen upload** → hard-delete (rij + bestanden weg)
        - **Admin verwijdert wat dan ook**   → soft-delete (in de prullenbak,
          recoverable via /admin/prullenbak)

    Bewoners kunnen sluiswachter-foto's of andermans foto's niet aanraken;
    die krijgen een 403.
    """
    photo = db.get_photo(photo_id)
    if not photo:
        abort(404)

    email = bewoner_auth.get_current_bewoner_email()
    is_admin = admin_auth.has_valid_admin_session()
    is_own = _is_bewoner_own_photo(photo, email)

    if not (is_own or is_admin):
        abort(403)

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if is_admin and not is_own:
        # Admin moderation: soft-delete zodat 'ie in de prullenbak komt
        # en hersteld kan worden als de admin per ongeluk klikte.
        db.soft_delete_photo(photo_id, deleted_by="admin")
        app.logger.info("Admin soft-deleted photo %s", photo_id)
        if is_ajax:
            return {"ok": True, "id": photo_id, "mode": "soft"}, 200
        return redirect(url_for("portaal_gallery"))

    # Bewoner haalt zijn eigen upload weg: hard-delete, vertrouwen we op.
    deleted = db.hard_delete_photo(photo_id)
    if deleted:
        photo_service.delete_files(
            deleted["filename"], deleted.get("thumb_filename")
        )
        app.logger.info("Bewoner %s hard-deleted photo %s", email, photo_id)

    if is_ajax:
        return {"ok": True, "id": photo_id, "mode": "hard"}, 200
    return redirect(url_for("portaal_gallery"))


@app.route("/portaal/aanvragen", methods=["GET", "POST"])
def portaal_aanvragen():
    """Public access-request form. No auth needed.
    Beschermd met Cloudflare Turnstile als CF_TURNSTILE_SITEKEY/SECRET
    in de env staan."""
    if request.method == "POST":
        voornaam = request.form.get("voornaam", "").strip()
        achternaam = request.form.get("achternaam", "").strip()
        email = request.form.get("email", "").strip().lower()
        motivatie = request.form.get("motivatie", "").strip() or None
        turnstile_token = request.form.get("cf-turnstile-response", "")

        # Basic validation
        if not voornaam or not achternaam or not email or "@" not in email:
            return render_template(
                "portaal/aanvragen.html",
                error="Vul je voornaam, achternaam en een geldig e-mailadres in.",
                voornaam=voornaam,
                achternaam=achternaam,
                email=email,
                motivatie=motivatie,
                turnstile_sitekey=cf_turnstile.CF_TURNSTILE_SITEKEY,
            )

        # Cloudflare Turnstile anti-bot check. No-op als niet geconfigureerd.
        # Cloudflare zet bij staging via Traefik vaak CF-Connecting-IP, val
        # terug op remote_addr als die er niet is.
        client_ip = request.headers.get("CF-Connecting-IP") or request.remote_addr
        if not cf_turnstile.verify_token(turnstile_token, remote_ip=client_ip):
            app.logger.warning("Turnstile-check faalde voor aanvraag van %s (IP %s)", email, client_ip)
            return render_template(
                "portaal/aanvragen.html",
                error="De anti-bot check is mislukt. Vink de Cloudflare-bevestiging aan en probeer opnieuw.",
                voornaam=voornaam,
                achternaam=achternaam,
                email=email,
                motivatie=motivatie,
                turnstile_sitekey=cf_turnstile.CF_TURNSTILE_SITEKEY,
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
                turnstile_sitekey=cf_turnstile.CF_TURNSTILE_SITEKEY,
            )

        request_id = db.save_access_request(email, voornaam, achternaam, motivatie)
        req = db.get_access_request(request_id)
        mail.send_access_request_notification(req)
        app.logger.info("New access request from %s", email)

        return redirect(url_for("portaal_aanvragen_bedankt"))

    return render_template(
        "portaal/aanvragen.html",
        turnstile_sitekey=cf_turnstile.CF_TURNSTILE_SITEKEY,
    )


@app.route("/portaal/aanvragen/bedankt")
def portaal_aanvragen_bedankt():
    """Confirmation page after submitting an access request."""
    return render_template("portaal/aanvragen_bedankt.html")


@app.route("/portaal/login", methods=["GET", "POST"])
def portaal_login():
    """Stap 1 van de magic-link flow: e-mail invoeren.

    Dit is OOK het admin-loginpad: als het admin-adres binnenkomt sturen
    we gewoon een OTP, en pas na de OTP-stap (in portaal_verify) wordt
    het admin-pad afgesplitst naar een wachtwoord-prompt. Externen kunnen
    daardoor niet aan de login zien of een adres bewoner of admin is.
    """
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

        is_admin = admin_auth.is_admin_email(email)
        is_bewoner = db.is_email_allowed(email)

        if not (is_admin or is_bewoner):
            return render_template(
                "portaal/login.html",
                error="Dit e-mailadres staat (nog) niet op de lijst.",
                email=email,
                show_request_link=True,
            )

        # Voor admin gebruiken we admin_auth.create_admin_otp (zelfde
        # tabel, andere mail). Voor bewoners de normale flow.
        if is_admin:
            code = admin_auth.create_admin_otp()
            mail.send_admin_otp_email(code)
            app.logger.info("Sent admin OTP to %s", email)
        else:
            code = bewoner_auth.create_and_save_otp(email)
            mail.send_otp_email(email, code)
            app.logger.info("Sent OTP to %s", email)

        return redirect(url_for("portaal_verify", email=email))

    return render_template("portaal/login.html")


@app.route("/portaal/verify", methods=["GET", "POST"])
def portaal_verify():
    """Stap 2: OTP-code invoeren. Daarna:
        - bewoner → meteen ingelogd
        - admin   → door naar wachtwoord-stap (/portaal/password)
    """
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

        # OTP klopt. Splits hier op rol.
        if admin_auth.is_admin_email(email):
            # Markeer email-factor als geslaagd, stuur naar password-stap.
            # Nog geen sessie, alleen een kortlopende tussen-cookie.
            response = make_response(redirect(url_for("portaal_password")))
            return admin_auth.set_otp_passed_cookie(response)

        # Reguliere bewoner: meteen sessie.
        response = make_response(redirect(url_for("portaal_index")))
        return bewoner_auth.set_bewoner_session_cookie(response, email)

    return render_template("portaal/verify.html", email=email)


@app.route("/portaal/password", methods=["GET", "POST"])
def portaal_password():
    """Stap 3 (alleen admin): wachtwoord invoeren. Vereist dat de
    OTP-stap zojuist geslaagd is (admin_otp_passed cookie)."""
    if not admin_auth.has_otp_passed():
        return redirect(url_for("portaal_login"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if not admin_auth.verify_admin_password(password):
            app.logger.warning("Admin wachtwoord-poging mislukt")
            return render_template(
                "portaal/password.html",
                error="Wachtwoord klopt niet. Probeer opnieuw of begin opnieuw met inloggen.",
            )
        # Beide factoren goed. Geef admin sessie + ook een bewoner-sessie
        # zodat de admin ook alle portaal-features kan gebruiken.
        response = make_response(redirect(url_for("portaal_index")))
        admin_auth.clear_otp_passed_cookie(response)
        admin_auth.set_admin_session_cookie(response)
        bewoner_auth.set_bewoner_session_cookie(response, admin_auth.ADMIN_EMAIL)
        app.logger.info("Admin login compleet")
        return response

    return render_template("portaal/password.html")


@app.route("/portaal/logout", methods=["POST"])
def portaal_logout():
    """Wist beide sessies (bewoner + admin) zodat één klik op uitloggen
    écht uitlogt, ook als je admin was."""
    response = make_response(redirect(url_for("portaal_login")))
    bewoner_auth.clear_bewoner_session_cookie(response)
    admin_auth.clear_admin_session_cookie(response)
    return response


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
        max_files=photo_service.MAX_FILES_PER_UPLOAD,
        max_file_bytes=photo_service.MAX_UPLOAD_BYTES,
        max_total_bytes=app.config["MAX_CONTENT_LENGTH"],
    )


def _handle_upload():
    """Process a multi-file POST. Saves what it can; reports the rest.
    Returnt JSON voor AJAX-calls, redirect voor plain form posts."""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    files = [f for f in request.files.getlist("photos") if f and f.filename]

    if not files:
        return _upload_error_response(
            "Geen foto's geselecteerd.", is_ajax,
            redirect_url=url_for("sluis_upload"),
        )

    if len(files) > photo_service.MAX_FILES_PER_UPLOAD:
        return _upload_error_response(
            f"Maximaal {photo_service.MAX_FILES_PER_UPLOAD} foto's tegelijk. "
            f"Je probeerde er {len(files)} te uploaden, splits het op in batches.",
            is_ajax,
            redirect_url=url_for("sluis_upload"),
        )

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
        except Exception:  # noqa: BLE001
            errors.append((f.filename, "Onverwachte fout, de beheerder kijkt ernaar."))
            app.logger.exception("Unexpected upload error for %s", f.filename)

    if not saved_ids:
        first_error = errors[0][1] if errors else "Onbekende fout."
        return _upload_error_response(
            first_error, is_ajax, redirect_url=url_for("sluis_upload"),
        )

    ok_param = ",".join(str(i) for i in saved_ids[:10])  # cap for URL length
    redirect_target = url_for("sluis_bedankt", ok=ok_param)
    if errors:
        redirect_target += f"&fail={len(errors)}"

    if is_ajax:
        return {
            "ok": True,
            "saved": len(saved_ids),
            "failed": len(errors),
            "errors": [{"file": fn, "msg": msg} for fn, msg in errors],
            "redirect": redirect_target,
        }, 200
    return redirect(redirect_target)


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
    and disappears from every public view. Admin kan herstellen of
    definitief verwijderen via /admin/prullenbak.

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
    """Sluis, bewoner, OF admin sessie geeft toegang tot /media/*."""
    return (
        has_valid_sluis_session()
        or bewoner_auth.has_valid_bewoner_session()
        or admin_auth.has_valid_admin_session()
    )


@app.route("/media/thumbs/<int:photo_id>")
def serve_thumb(photo_id: int):
    """Serve a thumbnail. Allowed for sluis, bewoner OR admin sessions.
    Admin mag ook soft-deleted thumbnails zien (voor de prullenbak-preview)."""
    if not _has_any_user_session():
        abort(403)
    photo = db.get_photo(photo_id)
    if not photo or not photo.get("thumb_filename"):
        abort(404)
    # Soft-deleted foto's alleen zichtbaar voor admin (prullenbak)
    if photo.get("deleted_at") and not admin_auth.has_valid_admin_session():
        abort(404)
    return send_from_directory(
        photo_service.THUMBS_DIR,
        photo["thumb_filename"],
        max_age=3600,
    )


@app.route("/media/photos/<int:photo_id>")
def serve_photo(photo_id: int):
    """Serve a full-size photo. Allowed for sluis, bewoner OR admin sessions.
    Admin mag ook soft-deleted foto's zien."""
    if not _has_any_user_session():
        abort(403)
    photo = db.get_photo(photo_id)
    if not photo:
        abort(404)
    if photo.get("deleted_at") and not admin_auth.has_valid_admin_session():
        abort(404)
    return send_from_directory(
        photo_service.PHOTOS_DIR,
        photo["filename"],
        max_age=3600,
    )


# ---------------------------------------------------------------------------
# Admin pages
# Login gaat via /portaal/login (unified flow), zie hierboven. Deze routes
# zijn alleen de admin-specifieke schermen, allemaal protected door
# require_admin decorator.
# ---------------------------------------------------------------------------

@app.route("/admin")
def admin_index():
    """Stuur door naar dashboard of de unified login."""
    if admin_auth.has_valid_admin_session():
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("portaal_login"))


@app.route("/admin/dashboard")
@admin_auth.require_admin
def admin_dashboard():
    """Stats + recent activity overzicht."""
    stats = db.admin_stats()
    top_uploaders = db.list_top_uploaders(limit=8)
    weekly = db.uploads_per_week(weeks=12)
    disk_usage = photo_service.directory_size_bytes()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        top_uploaders=top_uploaders,
        weekly=weekly,
        disk_usage=disk_usage,
        admin_email=admin_auth.ADMIN_EMAIL,
    )


@app.route("/admin/bewoners", methods=["GET", "POST"])
@admin_auth.require_admin
def admin_bewoners():
    """Lijst van bewoners + add-formulier."""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip() or None
        if email and "@" in email:
            db.add_allowed_resident(email, name=name, added_by="admin")
            app.logger.info("Admin voegde bewoner toe: %s", email)
        return redirect(url_for("admin_bewoners"))

    bewoners = db.list_allowed_residents()
    return render_template("admin/bewoners.html", bewoners=bewoners)


@app.route("/admin/bewoners/<path:email>/remove", methods=["POST"])
@admin_auth.require_admin
def admin_remove_bewoner(email: str):
    if db.remove_allowed_resident(email):
        app.logger.info("Admin verwijderde bewoner: %s", email)
    return redirect(url_for("admin_bewoners"))


@app.route("/admin/aanvragen")
@admin_auth.require_admin
def admin_aanvragen():
    """Openstaande toegangsaanvragen met 1-klik goedkeuren/weigeren."""
    aanvragen = db.list_pending_access_requests()
    return render_template("admin/aanvragen.html", aanvragen=aanvragen)


@app.route("/admin/aanvragen/<int:request_id>/approve", methods=["POST"])
@admin_auth.require_admin
def admin_approve_request(request_id: int):
    req = db.get_access_request(request_id)
    if not req:
        abort(404)
    # Voeg toe aan whitelist
    full_name = f"{req.get('voornaam', '')} {req.get('achternaam', '')}".strip()
    db.add_allowed_resident(
        req["email"], name=full_name or None, added_by="admin"
    )
    db.mark_access_request_handled(
        request_id, new_status="approved", handled_by="admin"
    )
    # Welkomst-mail naar de aanvrager met loginlink. Faalt gracefully:
    # als de mail niet de deur uitkomt blijft de toegang gewoon staan,
    # admin ziet 't dan in de logs.
    try:
        mail.send_access_approved_email(req)
    except Exception:  # noqa: BLE001
        app.logger.exception("Welkomst-mail mislukt voor %s", req["email"])
    app.logger.info("Admin keurde aanvraag goed voor %s", req["email"])
    return redirect(url_for("admin_aanvragen"))


@app.route("/admin/aanvragen/<int:request_id>/reject", methods=["POST"])
@admin_auth.require_admin
def admin_reject_request(request_id: int):
    req = db.get_access_request(request_id)
    if not req:
        abort(404)
    db.mark_access_request_handled(
        request_id, new_status="rejected", handled_by="admin"
    )
    app.logger.info("Admin weigerde aanvraag voor %s", req["email"])
    return redirect(url_for("admin_aanvragen"))


@app.route("/admin/prullenbak")
@admin_auth.require_admin
def admin_prullenbak():
    """Soft-deleted foto's terugkijken, herstellen of definitief verwijderen."""
    photos = db.list_soft_deleted_photos(limit=200)
    return render_template(
        "admin/prullenbak.html",
        photos=photos,
        total=db.count_soft_deleted_photos(),
    )


@app.route("/admin/prullenbak/<int:photo_id>/restore", methods=["POST"])
@admin_auth.require_admin
def admin_restore_photo(photo_id: int):
    if db.restore_photo(photo_id):
        app.logger.info("Admin herstelde foto %s", photo_id)
    return redirect(url_for("admin_prullenbak"))


@app.route("/admin/prullenbak/<int:photo_id>/purge", methods=["POST"])
@admin_auth.require_admin
def admin_purge_photo(photo_id: int):
    """Hard-delete één foto uit de prullenbak: rij weg, bestanden weg."""
    deleted = db.hard_delete_photo(photo_id)
    if deleted:
        photo_service.delete_files(
            deleted["filename"], deleted.get("thumb_filename")
        )
        app.logger.info("Admin purgede foto %s definitief", photo_id)
    return redirect(url_for("admin_prullenbak"))


@app.route("/admin/qr-poster")
@admin_auth.require_admin
def admin_qr_poster():
    """Preview-pagina voor de A4 QR-poster.
    Genereert een verse token zodat de admin live ziet wat 'ie krijgt;
    de daadwerkelijke download mint nogmaals een verse token zodat
    preview-URL en gedownloade URL identiek zijn van structuur (niet
    waarde, want elke generate_qr_token() levert een nieuwe signature)."""
    import base64
    import pdf_poster
    from tokens import generate_qr_token

    token = generate_qr_token()
    qr_url = f"{PUBLIC_BASE_URL}/sluis?t={token}"
    qr_png = pdf_poster._qr_png_bytes(qr_url, box_size=10)
    qr_data_url = "data:image/png;base64," + base64.b64encode(qr_png).decode("ascii")

    return render_template(
        "admin/qr_poster.html",
        qr_url=qr_url,
        qr_data_url=qr_data_url,
    )


@app.route("/admin/qr-poster/download.pdf", methods=["POST"])
@admin_auth.require_admin
def admin_qr_poster_download():
    """Mint een verse QR-token, render de A4 poster en stream als PDF.
    Filename bevat een datum zodat je makkelijk meerdere versies bewaart."""
    import pdf_poster
    from datetime import datetime, timezone
    from tokens import generate_qr_token

    token = generate_qr_token()
    qr_url = f"{PUBLIC_BASE_URL}/sluis?t={token}"
    pdf_bytes = pdf_poster.generate_qr_poster(qr_url)

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    app.logger.info("Admin downloadde QR-poster (%d bytes)", len(pdf_bytes))
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="sluiskade-qr-poster-{today}.pdf"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(_err):
    return render_template("errors/geen_toegang.html"), 403


@app.errorhandler(413)
def too_large(_err):
    """Triggered when the total request body exceeds MAX_CONTENT_LENGTH.
    Return JSON voor AJAX zodat de progress-UI 'm netjes kan tonen."""
    msg = "De foto's samen zijn te groot. Probeer er minder tegelijk of kleinere."
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"ok": False, "error": msg}, 413
    # Pak juiste redirect-target gebaseerd op waar 'ie vandaan kwam
    referrer = request.referrer or ""
    if "/portaal/" in referrer:
        return redirect(url_for("portaal_upload", error=msg))
    return redirect(url_for("sluis_upload", error=msg))


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


@app.cli.command("gen-admin-password-hash")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Wachtwoord (prompt is hidden, hash wordt geprint).",
)
def gen_admin_password_hash(password: str):
    """Genereer een werkzeug password-hash om in .env te zetten.

    Gebruik:
        flask gen-admin-password-hash
        (vul tweemaal het wachtwoord in)

    Kopieer de uitvoer in .env als:
        ADMIN_PASSWORD_HASH=pbkdf2:sha256:600000$....
    """
    if len(password) < 10:
        click.echo("  Wachtwoord moet minimaal 10 tekens zijn.")
        return
    h = generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)
    click.echo("")
    click.echo("Voeg deze regel toe aan .env (en aan Coolify environment):")
    click.echo("")
    click.echo(f"  ADMIN_PASSWORD_HASH={h}")
    click.echo("")
    click.echo("Het plaintext wachtwoord staat nergens opgeslagen, alleen deze hash.")
    click.echo("")


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
