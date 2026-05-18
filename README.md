# Sluiskade

Een kleine Flask-app voor het delen van bouwfoto's van een woningbouwproject aan de Sluiskade. Sluiswachters van de naastgelegen sluis schieten foto's vanuit het sluiswachtershuis en uploaden die via een QR-code; toekomstige bewoners loggen in met een magic link en zien hoe hun huis vorm krijgt.

**Live op:** [sluiskade.com](https://sluiskade.com)
**Stack:** Flask · SQLite · Pillow · Resend · APScheduler · Cloudflare Turnstile · Docker · Coolify

---

## Architectuur

Eén Flask-app, één SQLite-database, één foto-volume, met vier ingangen:

| Pad | Voor wie | Authenticatie |
|---|---|---|
| `/` | Iedereen | Publieke community one-pager |
| `/portaal` | Toekomstige bewoners | Magic link (e-mail OTP via Resend) |
| `/sluis` | Sluiswachters van de sluis ernaast | HMAC-getekend token in QR-code |
| `/admin` | Beheerder | Magic link + wachtwoord (2FA), via dezelfde /portaal/login flow |

Gehost op de droog.cloud stack (Contabo VPS + Coolify + Traefik + Let's Encrypt). Foto's worden gecomprimeerd, EXIF-GPS gestript, op een persistent Docker-volume opgeslagen en meegenomen in de nightly backup.

## Features

**Bewoners-portaal:** dashboard met stats, galerij, dag-gegroepeerde tijdlijn, autoplay timelapse met ken-burns animatie, hartjes, captions bij upload, downloads (per foto of als ZIP-bundle), random "verras me" knop, en eigen uploads hard-deleten.

**Sluiswachters:** QR-code in het sluiswachtershuis, multi-file upload met EXIF-strip en automatische thumbnails, eigen galerij en soft-delete.

**Admin:** lichte UI in dezelfde stijl als bewoners-portaal, dashboard met live stats (uploads/week chart, top-uploaders, opslagverbruik), 1-klik goedkeuren/weigeren van toegangsaanvragen (met automatische welkomst-mail), bewoner-beheer en prullenbak voor sluis-foto's.

**Achtergrond:** APScheduler-job ruimt elke nacht om 03:00 UTC soft-deletes ouder dan 30 dagen op (DB + bestanden van disk). Cloudflare Turnstile beschermt het publieke aanvraagformulier tegen bots.

## Waarom is dit publiek?

Dit is een persoonlijk project, maar de broncode staat open omdat ik geloof in transparantie en omdat ik zelf vaak leer van andermans repos. Voel je vrij om rond te neuzen, ideeën te lenen of het patroon over te nemen voor een eigen project.

Wat ik **niet** doe is issues en pull requests aannemen. Zie [`CONTRIBUTING.md`](CONTRIBUTING.md). Voor vragen of vondsten zie [`SECURITY.md`](SECURITY.md).

## Veiligheid

Alle secrets (HMAC-keys, API-tokens, password-hashes, session-keys) leven uitsluitend in environment variables op de productie-omgeving. Niets gevoeligs staat in deze repo. De `.env.example` toont welke variabelen verwacht worden, met dummy waardes. Zie ook [`SECURITY.md`](SECURITY.md).

## Licentie

[MIT](LICENSE). Wat je ermee doet is aan jou, met de gebruikelijke "no warranty" clausule.
