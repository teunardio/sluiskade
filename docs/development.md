# Development guide

Lokaal draaien, debuggen, en de CLI-commando's gebruiken zonder een productie-deploy te triggeren.

## Setup

Vereisten: Python 3.11+, git.

```bash
git clone git@github.com:teunardio/sluiskade.git
cd sluiskade

python3 -m venv .venv
source .venv/bin/activate    # of: .venv\Scripts\activate op Windows

pip install -r requirements.txt
```

Maak een lokale `.env`:

```bash
cp .env.example .env
```

Edit `.env` en zet **minimaal**:

```bash
SECRET_KEY=lokaal-iets-random
QR_TOKEN_SECRET=lokaal-iets-anders-random
ADMIN_EMAIL=jij@voorbeeld.nl       # gebruik echt iets dat je in test wil
DATA_PATH=./data                   # IMPORTANT: anders probeert 'ie /data
PUBLIC_BASE_URL=http://localhost:5000
```

Optioneel:

```bash
ADMIN_PASSWORD_HASH=               # leeg laten = admin-login geblokkeerd
RESEND_API_KEY=                    # leeg laten = mails naar stdout
CF_TURNSTILE_SITEKEY=              # leeg laten = aanvraag-form werkt zonder check
```

Starten:

```bash
flask run --debug
```

Open `http://localhost:5000`. Eerste run maakt `./data/sluiskade.db` aan met het complete schema.

### Admin lokaal kunnen inloggen

Genereer een password hash:

```bash
python3 bin/hash_password.py
```

Het script werkt **zonder** Flask app-init (geen DATA_PATH gedoe), heeft alleen `werkzeug` nodig. Vul tweemaal een wachtwoord in, krijg de hash terug, plak in `.env`:

```bash
ADMIN_PASSWORD_HASH=pbkdf2:sha256:600000$salt$hash
```

Voor lokale dev: **enkele** dollars (geen `$$` zoals voor Coolify). Restart `flask run` (of laat 'm via debug-mode reloaden).

Daarna inloggen via `http://localhost:5000/portaal/login` met je `ADMIN_EMAIL`. De OTP-code wordt geprint in je terminal (waar `flask run` draait), niet via Resend verstuurd:

```
============================================================
[mail.py DEV] No RESEND_API_KEY, would have sent:
  To:      jij@voorbeeld.nl
  From:    Sluiskade <admin@example.com>
  Subject: Admin-inlogcode Sluiskade: 472108
------------------------------------------------------------
... (volledige HTML van de mail)
```

Pak de 6-cijferige code uit Subject, plak in `/portaal/verify`, daarna je wachtwoord, klaar.

### Bewoner lokaal kunnen inloggen

```bash
flask add-bewoner --email=bewoner@voorbeeld.nl --name="Test Bewoner"
```

Daarna `/portaal/login` met dat email, OTP weer uit de terminal.

## Workflow tips

### Live reload

`flask run --debug` herstart bij elke `.py`-change. Voor template-changes hoeft er niks te herstarten (Jinja reload is automatic in debug-mode).

### Database resetten

Verwijder gewoon `./data/sluiskade.db`:

```bash
rm -rf ./data
mkdir ./data
flask run
```

Volgende request maakt 'm opnieuw aan via `db.init_db()`.

### Foto-uploads testen

Gebruik gewoon de Flask routes via je browser. Lokaal staan alle limits gelijk aan productie (10 files / 25 MB per / 150 MB totaal).

Voor command-line testen met curl:

```bash
# Bewoner sessie zetten (skip OTP-flow, voor automation):
# Niet praktisch, doe gewoon via browser

# Of via Flask test client in Python:
python3 -c "
from app import app
client = app.test_client()
# ... etc
"
```

### Email-flows testen zonder Resend

Standaard staat `RESEND_API_KEY` leeg → alle mails worden naar stdout geprint. Werkt voor OTPs, welkomstmails, admin-notificaties. Geen Resend account nodig om elke flow door te lopen.

Wil je écht een mail versturen vanaf je laptop tijdens dev: zet `RESEND_API_KEY=re_xxx` (test-key uit een dummy Resend account) en `MAIL_FROM` op een verified test-domain.

### Cloudflare Turnstile lokaal testen

Cloudflare biedt **officiële test-keys** die altijd dezelfde respons geven:

```bash
# Altijd-pass
CF_TURNSTILE_SITEKEY=1x00000000000000000000AA
CF_TURNSTILE_SECRET=1x0000000000000000000000000000000AA

# Altijd-fail (om error-state te zien)
CF_TURNSTILE_SITEKEY=2x00000000000000000000AB
CF_TURNSTILE_SECRET=2x0000000000000000000000000000000AA
```

Met deze keys werkt het widget lokaal op `localhost` (productie keys zijn aan een domein gekoppeld).

## CLI commands

Allemaal via `flask <command>` vanuit de project root.

| Command | Wat 't doet |
|---|---|
| `flask gen-qr-token` | Genereert een verse QR-token URL voor printen via externe tool (alternatief voor `/admin/qr-poster`) |
| `flask gen-admin-password-hash` | Vraagt om wachtwoord (hidden input), print werkzeug PBKDF2 hash voor in .env |
| `flask add-bewoner --email=X --name=Y` | Voegt email toe aan whitelist (idempotent: dubbel toevoegen no-ops) |
| `flask list-bewoners` | Toont alle whitelisted emails met naam + datum toegevoegd |
| `flask remove-bewoner --email=X` | Verwijdert uit whitelist (invalideert hun sessie meteen) |

### Standalone scripts (geen Flask app nodig)

`bin/hash_password.py` is een puur Python script met alleen `werkzeug` als dep, dus 't werkt zelfs als je de hele Flask-setup nog niet hebt:

```bash
pip3 install werkzeug
python3 bin/hash_password.py
```

Print twee hash-versies (gewone dollars voor `.env`, dubbele dollars voor Coolify).

### Handmatig auto-purge triggeren

```bash
python3 -c "import scheduler; print(scheduler.auto_purge_old_trash())"
```

Returnt een dict met counts. Geen wachten op 03:00 UTC nodig om de logica te testen.

## Sanity checks vóór een commit

```bash
# Python compile
python3 -m py_compile app.py db.py admin_auth.py bewoner_auth.py mail.py photo_service.py scheduler.py tokens.py cf_turnstile.py pdf_poster.py

# Templates compileren
python3 -c "
from jinja2 import Environment, FileSystemLoader
import os
env = Environment(loader=FileSystemLoader('templates'))
for r, _, fs in os.walk('templates'):
    for f in fs:
        if f.endswith('.html'):
            env.get_template(os.path.relpath(os.path.join(r,f), 'templates'))
print('All templates OK')
"

# Geen emoji's of em-dashes (project-conventie)
grep -rPn '[—\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}]' --include='*.html' --include='*.py' --include='*.md' . | head -5
```

Geen tests (yet). Sluiskade is klein genoeg dat een test-suite te veel overhead is voor de stabiliteit-winst. Validatie gebeurt via Flask test-client smoke-tests die ad-hoc gedraaid worden tijdens development.

## Conventies

### Geen emoji's in UI

Project-conventie: **nooit emoji-tekens** in templates, UI-strings, of code-comments die in HTML belanden. Reden: emojis renderen inconsistent across OS-en, zien er amateuristisch uit op een custom-designed page, en zijn niet stylable.

Gebruik in plaats daarvan de SVG-iconen uit `templates/_icons.html` (Lucide stijl, currentColor, sizable via prop):

```jinja
{% from "_icons.html" import icon %}
{{ icon("heart", size=16, stroke=2) }}
```

Nieuw icoon nodig? Voeg toe aan `_icons.html` als een nieuwe `{% elif name == "xyz" %}` case.

### Geen em-dashes

Em-dashes (`—`) wekken te erg een "AI-geschreven" gevoel. Gebruik in Nederlandse copy gewone middenpunten (`·`) of natuurlijke zinsbouw met komma's. Check vóór commit:

```bash
grep -rn '—' --include='*.html' --include='*.py' --include='*.md' .
```

### Brand kleuren

```css
--deep:  #0c4a6e   /* deep blue, koppen */
--water: #0ea5e9   /* sky blue, accent */
--foam:  #e0f2fe   /* lichte achtergrond */
--ink:   #0f172a   /* body text */
--paper: #f8fafc   /* card backgrounds */
--muted: #64748b   /* sub-text */
--amber: #b45309   /* admin badges, soft-delete acties */
```

Admin-paneel gebruikt dezelfde foam/water palette als bewoner-portaal, met amber accenten ter onderscheid. Geen dark-mode (bewuste keuze: licht past beter bij de "buiten in de bouwput" use-case).

## Project layout

Zie [README.md → Project-structuur](../README.md#project-structuur) voor de volledige tree. Op high-level:

- **Python modules** in de root (geen `src/` of `app/` package · Flask-app is klein genoeg)
- **Templates** in `templates/`, met subfolders per rol (`portaal/`, `sluis/`, `admin/`, `errors/`)
- **Static assets** in `static/` (favicons, manifest, sw.js, JS-widgets, hero images)
- **Documentation** in `docs/` (deze folder, plus README.md in root)
- **CLI scripts** in `bin/`
