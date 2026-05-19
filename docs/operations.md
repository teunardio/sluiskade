# Operations

Alles wat je nodig hebt om Sluiskade in productie te draaien, op te lossen wanneer 't kapot is, en up-to-date te houden.

## Hosting setup

- **VPS**: Contabo, draait Coolify (zelf-hosted Heroku-achtige)
- **Proxy**: Traefik met automatische Let's Encrypt cert renewal
- **Volume**: persistent `/data` mount voor SQLite + foto's
- **CDN/DNS**: Cloudflare (proxied A record)
- **Mail**: Resend voor outbound, Stalwart voor inbound (eigen mailbox op het domein)

Coolify auto-deployt op elke push naar `main` via GitHub webhook. Build pipeline runt `Dockerfile` → installeert Python deps → start gunicorn via `Procfile`.

## Deploy

### Normale code-deploy

```bash
cd ~/projects/sluiskade
git add .
git commit -m "Beschrijvende commit message"
git push
```

Coolify pakt 'm op binnen ~10 seconden, bouwt en swapt 'm zonder downtime. Eventuele build-errors zie je in **Deployments** tab. Faalt 'ie, dan blijft de oude versie draaien.

### Eerste install

1. Maak Coolify resource: **Application** → **Public repository** → `https://github.com/teunardio/sluiskade`
2. Build pack: **Dockerfile**
3. Persistent volume: mount `/data` met minstens 25 GB
4. Environment variables zetten (zie hieronder)
5. Domain koppelen: `sluiskade.com` → laat Coolify cert regelen
6. Deploy

### Environment variables

Alle vars staan met uitleg in [`.env.example`](../.env.example). Verplicht in productie:

| Variabele | Hoe te genereren |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `QR_TOKEN_SECRET` | Idem (apart van SECRET_KEY) |
| `QR_TOKEN_VERSION` | `1` (bump om alle QR's te invalideren) |
| `RESEND_API_KEY` | Maak aan op resend.com/api-keys |
| `MAIL_FROM` | `Sluiskade <admin@example.com>` (jouw eigen verified domain) |
| `ADMIN_EMAIL` | `admin@example.com` (zelfde adres als waar OTPs heen moeten) |
| `ADMIN_PASSWORD_HASH` | Zie [Admin wachtwoord instellen](#admin-wachtwoord-instellen) |
| `STORAGE_QUOTA_GB` | `20` |
| `DATA_PATH` | `/data` |
| `GOAL_TOTAL` | `1000` (target voor voortgangsbalk) |
| `PUBLIC_BASE_URL` | `https://sluiskade.com` |

Optioneel:

| Variabele | Waarvoor |
|---|---|
| `CF_TURNSTILE_SITEKEY` + `CF_TURNSTILE_SECRET` | Anti-bot op `/portaal/aanvragen` (fail-open zonder) |
| `PURGE_AFTER_DAYS` | Soft-delete retention (default 30) |

### Admin wachtwoord instellen

**In de Coolify container terminal:**

```bash
python3 bin/hash_password.py
```

Vul twee keer een sterk wachtwoord in (minimaal 10 tekens). Het script print **twee versies** naast elkaar:

```
Voor een lokaal .env bestand (gewone dollars):
  ADMIN_PASSWORD_HASH=pbkdf2:sha256:600000$salt$hash

Voor Coolify Environment Variables (dollars verdubbeld):
  ADMIN_PASSWORD_HASH=pbkdf2:sha256:600000$$salt$$hash
```

Plak de Coolify-versie (met `$$`) in Coolify Environment Variables → Save → **Restart container** (anders blijft de oude waarde geladen).

> **Waarom dubbele dollars?** Coolify gebruikt Docker Compose, dat enkele `$` interpreteert als variabele-expansie. PBKDF2 hashes bevatten `$` als scheider tussen method/iterations/salt/hash. Met `$$` wordt 't bij injectie weer omgezet naar enkele `$`.

Verificatie na restart (in container terminal):
```bash
python3 -c "
import os
h = os.environ.get('ADMIN_PASSWORD_HASH', '')
print('Lengte:', len(h), '(verwacht ~119)')
print('Aantal \$:', h.count('\$'), '(verwacht 2)')
"
```

### Cloudflare Turnstile

1. Cloudflare dashboard → **Turnstile** (onder "Trust and Safety")
2. **Add Site** → naam "Sluiskade aanvragen", domain `sluiskade.com`, mode **Managed**
3. Krijgt Site Key (publiek, `0x4AAA...`) en Secret Key
4. In Coolify env-vars zetten: `CF_TURNSTILE_SITEKEY` + `CF_TURNSTILE_SECRET`
5. Restart container

Geen dollars in hex-strings dus geen escape-issues. Niet ingesteld = fail-open (formulier blijft werken zonder bescherming).

## Monitoring

### Healthcheck

`GET /healthz` returnt `{"status": "ok"}` met 200. Gebruikt door:
- Docker `HEALTHCHECK` directive (in Dockerfile)
- Uptime Kuma op de droog.cloud monitoring stack (configureren via Uptime Kuma UI)

### Logs

Coolify → Sluiskade resource → **Logs** tab toont realtime gunicorn output. Belangrijke loglines om naar te zoeken:

- `Admin OTP requested for ...` · admin login attempt
- `Admin wachtwoord-poging mislukt` · failed password (mogelijk brute-force)
- `Admin keurde aanvraag goed voor ...` · access request approved
- `Bewoner ... hard-deleted photo ...` · eigen foto weggegooid
- `Auto-purge done: {...}` · daily APScheduler job summary
- `Turnstile-check faalde voor ...` · bot poging tegengehouden

### Stats

In admin paneel (`/admin/dashboard`) staat live een overzicht: foto's zichtbaar, uploads deze week, openstaande aanvragen, prullenbak-count, hartjes totaal, opslag-gebruik, uploads vandaag, plus een chart van uploads per week (12 weken). Voor diepere analyse: direct queries op de SQLite database.

### Postmaster Tools (deliverability)

Voor mail-deliverability monitoring zet je het volgende op:

- **Google Postmaster Tools** ([postmaster.google.com](https://postmaster.google.com)): gratis, vereist DNS TXT verificatie. Toont spam-rate, reputatie, authenticatie-stats voor mails naar Gmail/Workspace.
- **Microsoft SNDS** ([sendersupport.olc.protection.outlook.com](https://sendersupport.olc.protection.outlook.com)): vereist dat Stalwart's IP eraan gekoppeld is. Voor inzicht in Outlook/Hotmail-deliverability.

Beide leveren historische data · wil je inzicht over een week, zet ze nú aan.

## Backups

`/data` volume zit in de droog.cloud nightly backup-job (rsync naar offsite). Dat backupt:
- `sluiskade.db` (+ `-wal` + `-shm` files; consistente snapshot via SQLite's online backup API zou nog beter zijn maar voor onze write-frequency is rsync fine)
- `photos/` + `thumbs/` folders

### Handmatige backup (in container)

```bash
# SQLite consistent dumpen (vangt ook WAL-changes)
sqlite3 /data/sluiskade.db ".backup /tmp/sluiskade-$(date +%Y%m%d).db"

# Tar foto-volume
tar -czf /tmp/sluiskade-photos-$(date +%Y%m%d).tar.gz -C /data photos thumbs
```

### Restore

```bash
# Stop de app eerst om DB-locks te voorkomen
# In Coolify: Stop resource

# Vervang DB
cp /backup/sluiskade-20260518.db /data/sluiskade.db

# Vervang foto's
tar -xzf /backup/sluiskade-photos-20260518.tar.gz -C /data

# Start de app weer
# In Coolify: Start resource
```

## Troubleshooting

### "Admin login werkt niet, wachtwoord klopt 100%"

99% kans dat `ADMIN_PASSWORD_HASH` mishandeld is door Docker Compose dollar-expansie. Check in container:

```bash
python3 -c "
import os
h = os.environ.get('ADMIN_PASSWORD_HASH', '')
print('Lengte:', len(h), '(verwacht ~119)')
print('Aantal \$:', h.count('\$'), '(verwacht 2)')
"
```

Als `Aantal $: 1` → hash is corrupt. Fix: verdubbel `$` naar `$$` in Coolify env, restart.

### "Mail komt aan in spam"

Eerste paar weken na launch is normaal voor nieuwe domeinen. Verbetert vanzelf met goede engagement-signalen. Quick wins:

1. Stuur jezelf 3-5 OTPs naar Gmail/Outlook/iCloud, sleep ze uit spam, voeg de afzender toe aan je contacten
2. Zet Google Postmaster Tools aan voor monitoring
3. Voeg in de welkomstmail of bedankt-pagina een hint toe: "check ook je spam-map, ons domein is nieuw"

Zie verder [docs.droog.cloud/projects/sluiskade](https://docs.droog.cloud/projects/sluiskade) over de DKIM-setup (drie selectors, allemaal opzet).

### "Container start niet, error bij PRAGMA journal_mode"

Permissions op `/data`. Container draait als non-root user (uid 1000 meestal), en `/data` moet writable zijn. Coolify volume permissions wel goed gezet?

```bash
# In container, debug:
ls -ld /data
whoami
touch /data/test && echo "writable" || echo "NOT writable"
```

### "Scheduler draait niet / auto-purge gebeurt niet"

Check de logs voor `Scheduler: lead-worker, taken worden gepland.` Als die ontbreekt, draait er geen scheduler-instance. Mogelijke oorzaken:

- Lock-file probleem: `ls -la /data/scheduler.lock`
- Worker count = 0 (alleen master process, geen workers): check Procfile en gunicorn config

Handmatig triggeren om te valideren dat de job-code werkt:
```bash
python3 -c "import scheduler; print(scheduler.auto_purge_old_trash())"
```

### "Foto's komen niet door, geen error in logs"

Check Flask `MAX_CONTENT_LENGTH` (150 MB totaal) en de Traefik client_body_size. Default Traefik staat op ~10 MB. Voor Coolify met grote uploads moet je in de Traefik static config of via labels `--entryPoints.http.transport.respondingTimeouts.readTimeout` opvoeren, plus de body size.

### "Storage full" error

```bash
# Check disk usage in container
df -h /data
du -sh /data/photos /data/thumbs
```

Opties:
- Verhoog `STORAGE_QUOTA_GB` env-var (en daadwerkelijke volume size in Coolify)
- Trigger handmatige auto-purge om soft-deletes op te ruimen
- Admin haalt zwaar-gebruikers aan in galerij

## QR-code rotatie

Wil je alle bestaande geprinte QR-codes ongeldig maken (bv. een poster is gelekt op social media, of je verhuist naar een ander domein):

1. Coolify env-vars → `QR_TOKEN_VERSION` van `1` naar `2`
2. Restart container
3. Alle oude QR-codes geven nu 403
4. Genereer en print nieuwe poster(s) via `/admin/qr-poster`

Geen DB-acties nodig · de versie-mismatch wordt door HMAC-verificatie in `tokens.verify_qr_token()` opgevangen.

## Pre-deploy checklist (nieuwe release)

- [ ] `python3 -m py_compile app.py db.py admin_auth.py bewoner_auth.py mail.py photo_service.py scheduler.py tokens.py cf_turnstile.py pdf_poster.py`
- [ ] Jinja templates compileren: `python3 -c "from jinja2 import Environment, FileSystemLoader; e = Environment(loader=FileSystemLoader('templates')); import os; [e.get_template(os.path.relpath(os.path.join(r,f),'templates')) for r,_,fs in os.walk('templates') for f in fs if f.endswith('.html')]"`
- [ ] Geen emoji's of em-dashes: `grep -rPn '[—\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}]' --include='*.html' --include='*.py' .`
- [ ] Geen admin-email als placeholder in login-formulier (alleen als contact in `geen_toegang.html`)
- [ ] `.env.example` heeft alle nieuwe vars
- [ ] Eventuele DB-schema changes hebben migratie in `_apply_migrations()`
- [ ] `CACHE_NAME` in `static/sw.js` bumpen als statics gewijzigd zijn

Bij een grote release: bump ook de `requirements.txt` versies van security-gevoelige libs (werkzeug, itsdangerous, Pillow, Flask).

## Coolify resource configuratie (referentie)

```yaml
# Build pack
Dockerfile

# Persistent volumes
/data → 25 GB (foto's + DB)

# Domains
sluiskade.com (Let's Encrypt auto-renew)
www.sluiskade.com (Let's Encrypt auto-renew)

# Healthcheck
GET /healthz (interval 30s, timeout 5s)

# Environment variables
(zie .env.example voor lijst)
```
