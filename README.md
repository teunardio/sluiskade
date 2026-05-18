# Sluiskade

Een kleine Flask-app voor het delen van bouwfoto's van een woningbouwproject aan de Sluiskade. Sluiswachters van de naastgelegen sluis schieten foto's vanuit hun toren en uploaden die via een QR-code; toekomstige bewoners loggen in met een magic link en zien hoe hun huis vorm krijgt.

**Live op:** [sluiskade.com](https://sluiskade.com)
**Stack:** Flask · SQLite · Pillow · Resend · Authentik · Docker · Coolify

---

## Architectuur

Eén Flask-app, één SQLite-database, één foto-volume — vier ingangen:

| Pad | Voor wie | Authenticatie |
|---|---|---|
| `/` | Iedereen | Publieke community one-pager |
| `/portaal` | Toekomstige bewoners | Magic link (e-mail OTP via Resend) |
| `/sluis` | Sluiswachters van de sluis ernaast | HMAC-getekend token in QR-code |
| `/admin` | Beheerder | Authentik SSO (OIDC) |

Gehost op de droog.cloud stack (Contabo VPS + Coolify + Traefik + Let's Encrypt). Foto's worden gecomprimeerd, EXIF-GPS gestript, op een persistent Docker-volume opgeslagen en meegenomen in de nightly backup.

## Waarom is dit publiek?

Dit is een persoonlijk project, maar de broncode staat open omdat ik geloof in transparantie en omdat ik zelf vaak leer van andermans repos. Voel je vrij om rond te neuzen, ideeën te lenen of het patroon over te nemen voor een eigen project.

Wat ik **niet** doe is issues en pull requests aannemen — zie [`CONTRIBUTING.md`](CONTRIBUTING.md). Voor vragen of vondsten zie [`SECURITY.md`](SECURITY.md).

## Veiligheid

Alle secrets (HMAC-keys, API-tokens, OIDC-credentials, session-keys) leven uitsluitend in environment variables op de productie-omgeving. Niets gevoeligs staat in deze repo. De `.env.example` toont welke variabelen verwacht worden, met dummy waardes. Zie ook [`SECURITY.md`](SECURITY.md).

## Licentie

[MIT](LICENSE) — wat je ermee doet is aan jou, met de gebruikelijke "no warranty" clausule.
