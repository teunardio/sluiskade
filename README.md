# Sluiskade

Een Flask-app voor het delen van bouwfoto's van een nieuwbouwproject aan de Sluiskade in Kampen. Sluiswachters van de naastgelegen sluis schieten foto's vanuit het sluiswachtershuis en uploaden die via een QR-code; toekomstige bewoners loggen in met een magic link en zien hoe hun huis vorm krijgt.

**Live op:** [sluiskade.com](https://sluiskade.com)  
**Stack:** Flask · SQLite · Pillow · Resend · APScheduler · Cloudflare Turnstile · Docker · Coolify  
**Beheer-docs:** [docs.droog.cloud/projects/sluiskade](https://docs.droog.cloud/projects/sluiskade)

---

## Architectuur

Eén Flask-app, één SQLite-database, één foto-volume, vier ingangen:

| Pad | Voor wie | Authenticatie |
|---|---|---|
| `/` | Iedereen | Publieke community one-pager + access-request formulier |
| `/portaal` | Toekomstige bewoners | Magic link (e-mail OTP via Resend) |
| `/sluis` | Sluiswachters | HMAC-getekend token in QR-code |
| `/admin` | Beheerder | Magic link + wachtwoord (2FA), via dezelfde `/portaal/login` flow |

Gehost op de droog.cloud stack (Contabo VPS + Coolify + Traefik + Let's Encrypt). Foto's worden gecomprimeerd naar 1920px / q=82 progressive JPEG, EXIF-GPS gestript, op een persistent Docker-volume opgeslagen en meegenomen in de nightly backup.

## Features

**Bewoners-portaal:** dashboard met stats, galerij met search/filter, dag-gegroepeerde tijdlijn, autoplay timelapse met ken-burns animatie, hartjes, captions bij upload, downloads (per foto of als ZIP-bundle), random "verras me" knop, en eigen uploads hard-deleten.

**Sluiswachters:** QR-code in het sluiswachtershuis, multi-file upload met EXIF-strip en automatische thumbnails, eigen galerij en soft-delete.

**Admin:** lichte UI in dezelfde stijl als bewoners-portaal, dashboard met live stats (uploads/week chart, top-uploaders, opslagverbruik), 1-klik goedkeuren/weigeren van toegangsaanvragen (met automatische welkomst-mail), bewoner-beheer, prullenbak voor sluis-foto's, en een A4 PDF QR-poster generator.

**Achtergrond:** APScheduler-job ruimt elke nacht om 03:00 UTC soft-deletes ouder dan 30 dagen op (DB + bestanden van disk). Cloudflare Turnstile beschermt het publieke aanvraagformulier tegen bots. PWA service worker geeft offline cache voor foto's.

## Quickstart (lokaal)

```bash
git clone git@github.com:teunardio/sluiskade.git
cd sluiskade

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Belangrijk: zet DATA_PATH=./data zodat lokaal werkt (anders probeert 'ie /data)

flask run
```

Open http://localhost:5000. In dev-mode worden mails (OTP, welkomstmail, admin notificaties) naar stdout geprint ipv via Resend verstuurd, zodat je geen API-key nodig hebt om de flows te testen.

Voor de volledige development-instructies (CLI commands, tests, debug tips) zie [`docs/development.md`](docs/development.md).

## Project-structuur

```
sluiskade/
├── app.py                  # Flask routes + entry point
├── db.py                   # SQLite schema + helpers (foto's, likes, OTPs, bewoners)
├── photo_service.py        # Upload pipeline: EXIF strip, compressie, thumbnails
├── tokens.py               # QR-token signing + sluis session cookies
├── bewoner_auth.py         # Magic-link OTP + bewoner session cookies
├── admin_auth.py           # Admin 2FA (OTP + wachtwoord)
├── mail.py                 # Resend wrapper + email templates
├── scheduler.py            # APScheduler auto-purge job
├── pdf_poster.py           # A4 QR-poster generator (reportlab)
├── cf_turnstile.py         # Cloudflare Turnstile siteverify
├── bin/
│   └── hash_password.py    # Standalone admin password hash generator
├── docs/
│   ├── architecture.md     # DB schema, auth flows, photo pipeline
│   ├── operations.md       # Deploy, monitoring, troubleshooting, backups
│   ├── admin-guide.md      # Gebruikshandleiding admin paneel
│   └── development.md      # Lokaal draaien, tests, CLI commands
├── static/
│   ├── favicon*, icon-*    # PWA + browser icons
│   ├── manifest.webmanifest
│   ├── sw.js               # Service worker (served via /sw.js route)
│   ├── upload-widget.js    # Client-side upload met progress
│   └── images/             # Hero foto's voor publieke one-pager
├── templates/
│   ├── _head_meta.html     # Shared <head> include (favicons, SW reg, fonts)
│   ├── _icons.html         # SVG icon library (Lucide stijl)
│   ├── index.html          # Publieke one-pager
│   ├── portaal/            # Bewoner + admin login/views
│   ├── sluis/              # Sluiswachter views
│   ├── admin/              # Admin dashboard, bewoners, aanvragen, prullenbak, qr-poster
│   └── errors/             # 403, etc.
├── Dockerfile
├── requirements.txt
├── Procfile                # Gunicorn voor productie
└── .env.example            # Alle environment variabelen met uitleg
```

## Environment variables

Zie [`.env.example`](.env.example) voor alle variabelen met uitleg. Korte versie van wat verplicht is in productie:

- `SECRET_KEY` · Flask session signing (64-char hex)
- `QR_TOKEN_SECRET` + `QR_TOKEN_VERSION` · HMAC voor QR-codes
- `RESEND_API_KEY` + `MAIL_FROM` · Transactional email
- `ADMIN_EMAIL` + `ADMIN_PASSWORD_HASH` · Admin 2FA (zie [docs/operations.md](docs/operations.md#admin-wachtwoord-instellen) voor hash-generatie)
- `STORAGE_QUOTA_GB` + `DATA_PATH` · Foto-opslag limieten

Optioneel:
- `CF_TURNSTILE_SITEKEY` + `CF_TURNSTILE_SECRET` · Anti-bot op aanvraagformulier (fail-open zonder)
- `PURGE_AFTER_DAYS` · Soft-delete retention (default 30)
- `PUBLIC_BASE_URL` · Voor links in mails

## Documentation

Diepere documentatie in deze repo:

- [`docs/architecture.md`](docs/architecture.md) · DB schema, auth flows, photo pipeline, file storage
- [`docs/operations.md`](docs/operations.md) · Deploy, monitoring, troubleshooting, backups, env-vars
- [`docs/admin-guide.md`](docs/admin-guide.md) · Hoe gebruik je het admin paneel
- [`docs/development.md`](docs/development.md) · Lokaal draaien, tests, CLI commands

Beheer en infra-context (DNS, Coolify, Stalwart, Cloudflare): [docs.droog.cloud](https://docs.droog.cloud).

## Waarom is dit publiek?

Persoonlijk project, broncode open omdat ik geloof in transparantie en omdat ik zelf vaak leer van andermans repos. Voel je vrij om rond te neuzen, ideeën te lenen of het patroon over te nemen voor een eigen project.

Wat ik **niet** doe is issues en pull requests aannemen. Zie [`CONTRIBUTING.md`](CONTRIBUTING.md). Voor security-vondsten zie [`SECURITY.md`](SECURITY.md).

## Veiligheid

Alle secrets (HMAC-keys, API-tokens, password-hashes, session-keys) leven uitsluitend in environment variables op de productie-omgeving. Niets gevoeligs staat in deze repo. De `.env.example` toont welke variabelen verwacht worden, met dummy waardes. Foto-uploads worden EXIF-gestript (GPS verwijderd), magic-byte gecheckt (geen videos), MIME-whitelist gehandhaafd. Het admin-account gebruikt 2FA (email OTP + werkzeug PBKDF2 wachtwoord-hash). Zie ook [`SECURITY.md`](SECURITY.md).

## Licentie

[MIT](LICENSE). Wat je ermee doet is aan jou, met de gebruikelijke "no warranty" clausule.
