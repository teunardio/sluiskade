# Architectuur

Sluiskade is een **monolithische Flask-app** met SQLite als state-store en een persistent foto-volume. Geen aparte services, geen message-queues, geen Redis. Past op één kleine VPS, schaalt prima voor honderden bewoners en duizenden foto's.

## High-level

```
                       sluiskade.com
                            │
        ┌──────────┬────────┼─────────┬──────────┐
        │          │        │         │          │
        /        /portaal  /sluis   /admin    /aanvragen
   (one-pager) (bewoner)  (QR)    (admin)    (publiek + Turnstile)
        │          │        │         │          │
        └──────────┴────────┴─────────┴──────────┘
                            │
                    Flask app (app.py)
                    │            │
              SQLite WAL    /data/photos/
              (sluiskade.db) /data/thumbs/
                    │
              APScheduler (03:00 UTC, daily)
                    └─ auto-purge soft-deletes >30 dagen
```

Drie distincte sessie-cookies, allemaal HMAC-gesigneerd via `itsdangerous`:

| Cookie | Wie | TTL | Salt |
|---|---|---|---|
| `sluis_session` | Sluiswachter na QR-scan | 12 uur | `sluis-session-v1` |
| `bewoner_session` | Bewoner na OTP | 30 dagen (rolling) | `bewoner-session-v1` |
| `admin_session` | Admin na 2FA | 8 uur | `admin-session-v1` |
| `admin_otp_passed` | Korte tussen-cookie (admin) | 10 minuten | `admin-otp-passed-v1` |

Aparte salts zodat een lek van één cookie geen andere kan namaken. Cookie-paths staan op `/` zodat ze meegaan naar `/media/*`-routes voor authorized thumbnail/photo serving.

## Database

SQLite (`/data/sluiskade.db`) in WAL-modus voor betere read-concurrency tijdens uploads. Schema in [`db.py`](../db.py).

### Tabellen

```sql
-- Alle foto's (soft-delete via deleted_at)
photos (
    id              INTEGER PRIMARY KEY,
    filename        TEXT NOT NULL UNIQUE,
    thumb_filename  TEXT,
    source          TEXT NOT NULL CHECK(source IN ('sluis','bewoner','admin')),
    uploader_email  TEXT,                  -- NULL voor sluis-uploads
    width, height, file_size INTEGER,
    caption         TEXT,                  -- optioneel, max 240 chars
    uploaded_at     TEXT NOT NULL,
    deleted_at      TEXT,                  -- soft-delete marker
    deleted_by      TEXT,                  -- 'sluis' of 'admin'
    deleted_reason  TEXT                   -- optioneel
)

-- Hartjes (uniek per foto + email)
photo_likes (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos ON DELETE CASCADE,
    bewoner_email TEXT NOT NULL COLLATE NOCASE,
    liked_at TEXT NOT NULL,
    UNIQUE(photo_id, bewoner_email)
)

-- Whitelist van toegelaten bewoners
allowed_residents (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT, added_at TEXT, added_by TEXT
)

-- Toegangsaanvragen vanuit /aanvragen
access_requests (
    id INTEGER PRIMARY KEY,
    email, voornaam, achternaam, motivatie TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
    requested_at, handled_at, handled_by TEXT
)

-- Eenmalige codes voor magic-link login (bewoner + admin gebruiken zelfde tabel)
bewoner_otps (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE,
    code TEXT NOT NULL,                    -- 6-digit numeric
    expires_at TEXT NOT NULL,              -- 15 min na creation
    used_at TEXT,                          -- markeert verbruikt
    created_at TEXT
)
```

### Migraties

`db._apply_migrations()` runt na `init_db()` en checkt via `PRAGMA table_info` of nieuwe kolommen al bestaan. Voorbeeld: `caption` op `photos` werd later toegevoegd via een `ALTER TABLE`. SQLite heeft geen `IF NOT EXISTS` voor kolommen dus check je 'm zelf.

Bij nieuwe schema-wijzigingen: voeg de kolom toe aan de `SCHEMA` constant (voor verse installs) én aan `_apply_migrations()` (voor bestaande databases).

## Auth flows

### Sluiswachter · QR-token (stateless)

```
1. Admin draait /admin/qr-poster → PDF download met QR
2. QR bevat: https://sluiskade.com/sluis?t=<HMAC-signed-token>
3. Sluiswachter scant met telefoon
4. /sluis route:
   - tokens.verify_qr_token() checkt HMAC + version + age (<5 jaar)
   - Bij OK: 12u sluis_session cookie + redirect /sluis/upload
   - Bij fout: 403 → renders templates/errors/geen_toegang.html
5. Returnende sluiswachter binnen 12u: cookie volstaat, geen scan nodig
```

Tokens zijn **stateless** · geen DB-rij, geen revocatie per token. Alle ooit-gegenereerde tokens met dezelfde `QR_TOKEN_SECRET` + `QR_TOKEN_VERSION` blijven geldig. Nuclear option: bump `QR_TOKEN_VERSION` in Coolify env-vars, restart container, alle bestaande QR's dood.

### Bewoner · Magic-link OTP

```
1. Bewoner → /portaal/login → vult email in
2. Server checkt db.is_email_allowed(email) of admin_auth.is_admin_email(email)
3. Bij niet-match: zelfde foutmelding tonen (anti-enumeration)
4. Bij match: bewoner_auth.create_and_save_otp() genereert 6-digit code,
   schrijft naar bewoner_otps tabel met expires_at = now + 15min
5. mail.send_otp_email() stuurt code via Resend
6. Bewoner → /portaal/verify → vult code in
7. Server: bewoner_auth.verify_and_consume_otp() valideert + markeert used_at
8. Bij OK: bewoner_session cookie (30 dagen rolling) + redirect /portaal
```

`has_valid_bewoner_session()` checkt **zowel** geldige cookie-signature **als** of de email nog op de whitelist staat. Een bewoner verwijderen uit `allowed_residents` invalideert hun sessie meteen, ook al is de cookie nog niet verlopen. Admin-email is een uitzondering · die hoeft niet in de whitelist te staan om door deze check te komen.

### Admin · 2FA via unified flow

```
1. Admin → /portaal/login → vult ADMIN_EMAIL in
2. Server: admin_auth.is_admin_email() returns True
3. admin_auth.create_admin_otp() (hergebruikt bewoner_otps tabel)
4. mail.send_admin_otp_email() (andere template dan bewoner-OTP)
5. Admin → /portaal/verify → vult code in
6. Server: admin_auth.verify_admin_otp() valideert
7. Bij OK: admin_otp_passed cookie (10min, scope=/portaal) + redirect /portaal/password
8. Admin → /portaal/password → vult wachtwoord in
9. Server: admin_auth.verify_admin_password() doet werkzeug.check_password_hash
   tegen ADMIN_PASSWORD_HASH (PBKDF2:sha256)
10. Bij OK: admin_session cookie (8u) + bewoner_session cookie + redirect /portaal
```

Cookie-paths zijn cruciaal: `admin_otp_passed` heeft path `/portaal` zodat 'ie meegaat naar `/portaal/password`. Vroeger stond 'ie op `/admin` waardoor de password-stap nooit kon valideren (cookie kwam niet mee).

Anti-enumeration zit op meerdere lagen: foute én juiste email-input op `/portaal/login` resulteren in dezelfde redirect naar `/portaal/verify`. Foutieve OTPs geven dezelfde foutmelding ongeacht of de email matched.

### Toegangsaanvraag · publiek + Turnstile

```
1. Niet-whitelisted bezoeker → /portaal/aanvragen formulier
2. Cloudflare Turnstile widget (challenges.cloudflare.com)
3. Submit POST naar /portaal/aanvragen met cf-turnstile-response token
4. Server: cf_turnstile.verify_token() doet siteverify-call
5. Bij fail: error redirect (fail-open als CF_TURNSTILE_* niet geconfigureerd)
6. db.save_access_request() schrijft naar access_requests tabel
7. mail.send_access_request_notification() naar ADMIN_EMAIL met CTA naar /admin/aanvragen
8. Redirect naar /portaal/aanvragen/bedankt
```

Admin keurt goed via `/admin/aanvragen` (1-klik knop):
- `db.add_allowed_resident()` voegt toe aan whitelist
- `db.mark_access_request_handled(status='approved')`
- `mail.send_access_approved_email()` stuurt welkomstmail met loginlink

## Foto-pipeline

Elke upload (`photo_service.save_photo()`) doorloopt 10 stappen in deze volgorde · bewust, want sommige stappen zijn goedkoper-eerst en sommige hangen af van vorige resultaten:

```
1. Read full file into bytes (cap door Flask MAX_CONTENT_LENGTH=150MB)
2. Per-file size cap (MAX_UPLOAD_BYTES=25MB)
3. Magic-byte sniff via python-magic (echte MIME, niet de claim)
4. MIME whitelist: image/{jpeg,png,webp,heic,heif}
   → Video MIMEs krijgen aparte fout, alles anders generic reject
5. Storage quota check (STORAGE_QUOTA_GB=20 default)
6. Pillow Image.open() + img.load() (corruptie hier vangen)
7. ImageOps.exif_transpose (iPhone portretten rechtop)
8. Convert naar RGB (HEIC, PNG-alpha → witte achtergrond)
9. Downscale naar MAX_DIMENSION=1920 (LANCZOS)
10. Save als progressive JPEG q=82 + 480px thumb q=78
    → Re-encoding strip ALL EXIF inclusief GPS coords (privacy)
```

Resultaat per foto: ~250-450 KB origineel + ~25-50 KB thumb.

### Bestandsnamen

`<uuid4-hex>.jpg` voor origineel, `<uuid4-hex>_thumb.jpg` voor thumbnail. Geen relatie met originele bestandsnaam (privacy + collision-vermijding). Mapping uuid → metadata zit in de `photos` tabel.

### Storage layout

```
/data/
├── sluiskade.db                    # SQLite (WAL: -wal en -shm files erbij)
├── scheduler.lock                  # APScheduler fcntl lock
├── photos/
│   ├── 5f8a3c2b1e9d.jpg            # originelen (1920px, q=82)
│   └── ...
└── thumbs/
    ├── 5f8a3c2b1e9d_thumb.jpg      # thumbs (480px, q=78)
    └── ...
```

Persistent Docker volume gemount op `/data`. Backup via standaard droog.cloud nightly job (rsync naar offsite).

## Media serving

Auth-gated routes voor het serven van foto's:

```python
GET /media/thumbs/<photo_id>   → /data/thumbs/<thumb_filename>
GET /media/photos/<photo_id>   → /data/photos/<filename>
```

Toegang: sluis_session **of** bewoner_session **of** admin_session. Voor admin: ook soft-deleted foto's zichtbaar (anders kan je in `/admin/prullenbak` geen preview zien). Voor anderen: soft-deletes geven 404.

Pre-signed URLs of S3 zou voor groei waardevol zijn · voor nu doet Flask + sendfile prima voor honderden requests per dag.

## Background scheduler

`scheduler.py` gebruikt APScheduler in BackgroundScheduler mode. Job draait dagelijks om 03:00 UTC:

```python
db.purge_old_soft_deletes(days=PURGE_AFTER_DAYS)  # default 30
→ Returns list van rows die net verwijderd zijn
→ Per row: photo_service.delete_files() removet bestanden van disk
→ Log naar stdout met counts
```

**Gunicorn-safe**: zonder bescherming zou elke worker de scheduler starten. Oplossing in `scheduler._try_acquire_lock()`: `fcntl.flock` op `/data/scheduler.lock`, alleen de winnende worker activeert de scheduler. Andere workers slaan 't stilletjes over. Lock wordt automatisch vrijgegeven bij process exit (atexit-hook).

Voor handmatige trigger (test of na een grote opruim):
```bash
python3 -c "import scheduler; print(scheduler.auto_purge_old_trash())"
```

## Service worker (PWA)

`static/sw.js` wordt geserveerd via `/sw.js` route (Flask) zodat 'ie scope `/` heeft. Cache-strategie:

| Route-prefix | Strategie | Reden |
|---|---|---|
| `/static/*` | Cache-first + background revalidate | Veranderlijk maar zelden |
| `/media/*` | Cache-first | Foto IDs zijn uniek, content nooit verandert |
| HTML pagina's | Network-first, cache als offline fallback | Verse content wint |
| `/portaal/login`, `/verify`, `/password`, `/admin/*`, `/sluis/*` | **NEVER CACHE** | Gevoelig of state-veranderend |

Op deploy van een nieuwe versie: bump `CACHE_NAME` in `sw.js` (bv `sluiskade-v1` → `sluiskade-v2`). Bij activate-event delete de SW oude caches. Tussendoor doet de background-revalidate z'n werk.

Browser-install support via standaard manifest + apple-touch-icon. Geen in-app prompt (bewust niet · afleidend voor gebruikers die 't niet willen).

## Tech-keuzes (waarom dit zo)

| Keuze | Alternatief overwogen | Reden voor de keuze |
|---|---|---|
| SQLite | Postgres | App is single-instance, lage write-frequency, geen replication nodig. SQLite + WAL is meer dan genoeg en saves a managed DB |
| Stateless tokens | Token table met revocation | Posters hangen voor maanden, revocation per token zou DB-state én een UI vragen. Version-bump is een fijne nuclear option |
| Resend | SMTP via Stalwart | Aparte sender voor transactional mail is sneller in te stellen + betere deliverability via dedicated infra |
| werkzeug PBKDF2 | bcrypt/argon2 | Komt mee met Flask, geen extra deps, voor single-admin-account snel zat |
| APScheduler in-process | cron / systemd timer | Geen extra container-orkestrering nodig, draait in dezelfde Python proces |
| Geen Authentik | OIDC zoals droog.family | Voor één admin-account is OIDC een sloopkogel om een schroef in te draaien |
| Cloudflare Turnstile | reCAPTCHA / hCaptcha | Privacy-vriendelijk, gratis, vaak non-interactief |
| Pillow + python-magic | Imagemagick CLI | Native Python, geen subprocess overhead, betere error messages |
| MkDocs in aparte repo | Inline docs/ folder | Heb een docs.droog.cloud site, daar past 't logischer dan in app-repo |
