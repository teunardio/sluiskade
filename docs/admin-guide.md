# Admin guide

Hoe je het Sluiskade-beheerpaneel gebruikt · voor wanneer je het paar weken niet hebt aangeraakt en niet meer weet welke knop wat doet.

## Inloggen

Ga naar `https://sluiskade.com/portaal/login` (geen aparte `/admin/login` · zelfde flow als bewoners).

1. Vul `beheer@sluiskade.com` in
2. Check de inbox van die mailbox voor een 6-cijferige code
3. Vul de code in
4. Vul je admin-wachtwoord in
5. Je landt op `/portaal` (bewoner-dashboard) met een bruin **"Admin"** badge rechts in de topbar
6. Klik op die badge om naar `/admin/dashboard` te gaan

Je hebt nu **8 uur** admin-sessie + 30 dagen bewoner-sessie. Logout (rechtsboven) clear beide.

## Dashboard

`/admin/dashboard`. Live overzicht:

- **Foto's zichtbaar** · alles dat niet soft-deleted is
- **Deze week** · uploads in de afgelopen 7 dagen
- **Bewoners** · aantal whitelisted email-adressen
- **Aanvragen open** · pending toegangsverzoeken (amber als > 0)
- **In prullenbak** · soft-deleted, wachten op handmatige of auto-purge
- **Hartjes totaal** · alle likes opgeteld
- **Opslag in gebruik** · GB/MB van het `/data` volume
- **Vandaag binnen** · uploads in laatste 24 uur

Plus een **uploads-per-week chart** (laatste 12 weken, hover voor exacte aantallen) en een **top-uploaders tabel**.

Als er aanvragen open staan zie je een amber alert-strook met "Bekijken" knop direct naar de aanvragen-pagina.

## Bewoners beheren

`/admin/bewoners`. Lijst van alle email-adressen die mogen inloggen op het portaal.

**Toevoegen:** vul email + optioneel naam in, klik Toevoegen. Persoon kan vanaf nu zelf inloggen via `/portaal/login` (krijgen een OTP gemaild bij elke login).

> **Let op:** het toevoegen van een bewoner stuurt **geen** welkomstmail. De persoon weet dus zelf nog niet dat 'ie toegang heeft. Voor formele toegangsverlening: gebruik **Aanvragen** met goedkeur-knop (die stuurt wel een welkomstmail).

**Verwijderen:** rode "Verwijder" knop achter elke regel. Vraagt om bevestiging. Verwijderen invalideert hun sessie **meteen** · ook al hadden ze een geldige cookie, de check op de whitelist faalt vanaf nu.

Hun eerder geüploade foto's blijven staan (de `uploader_email` field blijft kloppen). Wil je die ook weghalen, doe dat los via de galerij of de prullenbak.

## Toegangsaanvragen

`/admin/aanvragen`. Mensen die het publieke aanvraagformulier op `/portaal/aanvragen` hebben ingevuld, staan hier in afwachting.

Per aanvraag zie je: naam, email, motivatie, wanneer ingediend.

**Goedkeuren** (groene knop, 1-klik):
- Voegt email toe aan whitelist
- Markeert aanvraag als `approved`
- Stuurt automatisch een welkomstmail naar de aanvrager met loginlink + uitleg

**Weigeren** (rode knop, vraagt confirm):
- Markeert aanvraag als `rejected`
- Stuurt **geen** mail (bewuste keuze: silently rejecting is meestal wat je wil bij spam of foutieve aanvragen)
- Aanvraag verdwijnt uit de pending-lijst

Als je een afgewezen persoon alsnog wil toelaten: voeg ze handmatig toe via `/admin/bewoners`.

## Prullenbak (sluis-foto's)

`/admin/prullenbak`. Soft-deleted foto's, gegrayscaled getoond met datum + door wie verwijderd.

**Herstel** (groene knop):
- Foto wordt weer zichtbaar in galerij + tijdlijn + timelapse
- Volledig reversibel, kost niets

**Definitief** (rode knop, vraagt confirm):
- Database-rij gaat weg
- Bestand én thumbnail van schijf verwijderd
- Onomkeerbaar · zorg dat je 't echt wil

**Auto-purge** ruimt soft-deletes ouder dan `PURGE_AFTER_DAYS` (default 30) automatisch op via een nachtelijke APScheduler-job. Heb je dus geen haast om alles handmatig te legen.

## Foto's modereren

Naast de prullenbak kun je vanuit de gewone bewoner-galerij elke foto **soft-deleten**:

1. Ga naar `/portaal/gallery`
2. Hover over een foto
3. Klik het **amber trash-icoontje** rechtsboven op de thumb (alleen zichtbaar als admin)
4. Klik "Ja" in het amber confirm-overlay

De foto verdwijnt direct uit de galerij voor iedereen, en komt in de prullenbak waar je 'm later kunt herstellen of definitief weggooien. Geen email-notificatie, geen audit-log (yet).

Sluiswachters kunnen hun eigen foto's ook soft-deleten via `/sluis/gallery`, die komen in dezelfde prullenbak.

Bewoners kunnen alleen **hun eigen** uploads weggooien (hard-delete, direct weg, geen prullenbak). Daar grijp je niet in, dat is hun data.

## QR-poster genereren

`/admin/qr-poster`. Voor in het sluiswachtershuis.

1. Bekijk de HTML-mockup links (laat zien hoe de PDF eruit komt te zien)
2. Lees de print-tips rechts (papier, plaatsing op de ruit, scan-test)
3. Klik **Download A4 poster (PDF)**
4. Een PDF met naam `sluiskade-qr-poster-<datum>.pdf` wordt gedownload
5. Print 'm op gewoon A4 wit papier
6. **Scan zelf eerst met je telefoon** voordat je 'm meeneemt · om te checken dat de QR werkt en je op `/sluis/upload` landt

Elke download genereert een **nieuwe verse QR-token**. Oude posters blijven werken (alle tokens zijn geldig tot je `QR_TOKEN_VERSION` bumpt). Dus geen paniek als je 'm twee keer print · gewoon allebei werken.

Wil je alle bestaande posters in één klap invalideren (poster gestolen, jouw site krijgt rare uploads): bump `QR_TOKEN_VERSION` in Coolify env-vars (1 → 2), restart container, print nieuwe posters. Zie [operations.md → QR-code rotatie](operations.md#qr-code-rotatie).

## Veelvoorkomende taken

### Bewoner kan niet inloggen

1. Check `/admin/bewoners` of hun email op de lijst staat
2. Zo niet, voeg toe · en stuur ze handmatig een berichtje dat ze kunnen inloggen (geen auto-mail bij handmatige add)
3. Wel op de lijst maar OTP komt niet aan? Check spam-folder. Of vraag ze om beheer@sluiskade.com aan contacten toe te voegen. Mocht het structureel zijn: check Resend dashboard voor delivery-status

### Sluiswachter belt op met "QR werkt niet"

1. Check of `QR_TOKEN_VERSION` recent gebumpt is (Coolify env-vars). Zo ja, geef ze een nieuwe poster
2. Vraag of ze "geen toegang" pagina zien · dan is de cookie verlopen, gewoon opnieuw scannen
3. Vraag of ze de QR sowieso kunnen scannen met hun camera-app (test op een random andere QR-code) · soms ligt 't aan hun telefoon
4. Mocht 't echt aan ons liggen: check Coolify logs voor verificatie-errors

### Iemand heeft een ongewenste foto geüpload

1. Ga naar `/portaal/gallery`
2. Klik op de foto → opent foto-detail
3. Klik **"Naar prullenbak"** (amber knop linksboven)
4. Foto staat nu in `/admin/prullenbak`, klik **Definitief** om 'm écht weg te gooien

### Disk raakt vol

1. Check `/admin/dashboard` voor opslag-stat
2. Open `/admin/prullenbak` en gooi handmatig wat oudere soft-deletes definitief weg (snelste win)
3. Of wacht op de nachtelijke auto-purge job
4. Voor structurele groei: verhoog `STORAGE_QUOTA_GB` env-var én het volume in Coolify

## Wat je niet kunt vanuit het admin-paneel (nog)

Dingen die handmatig via Coolify container terminal moeten:

- Resend API key roteren → Coolify env-vars
- Admin wachtwoord wijzigen → `python3 bin/hash_password.py` + env-var update
- QR-token version bumpen → Coolify env-vars
- Backups maken/restoren → handmatig (zie [operations.md → Backups](operations.md#backups))
- SQLite queries voor diepere analyses → `sqlite3 /data/sluiskade.db`

Mogelijk gepland voor Sprint 5:
- Audit-log van admin-acties (wie deed wat wanneer)
- Albums + deelbare publieke links
- Gamification (badges, voortgangsbalk, confetti bij milestones)
