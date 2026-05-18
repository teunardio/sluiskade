# Security

## Een kwetsbaarheid melden

Als je een beveiligingsprobleem vindt — een lek waardoor secrets uitlekken, een manier om de QR-token-validatie te omzeilen, een upload-route die meer accepteert dan zou moeten, of iets anders dat mij in de problemen kan brengen — meld het dan **niet** in een publieke issue.

Stuur in plaats daarvan een e-mail naar **hello@teunard.com** met:

- Een korte beschrijving van het probleem
- Stappen om het te reproduceren
- Eventueel een idee voor de fix

Ik reageer binnen een paar dagen. Verantwoorde melders krijgen credit in de release notes als ze dat willen.

## Wat in deze repo *niet* staat

- Geen API-keys, geheime tokens of credentials van welke soort dan ook
- Geen productiedata (foto's, e-mailadressen, OTP's, sessies)
- Geen `.env`-bestanden — alleen `.env.example` met dummy waardes
- Geen SQLite-databases — die leven op de productie-VPS

GitHub's secret scanning en push protection staan aan, dus per ongeluk commit van een echte key wordt geblokkeerd voordat hij doorkomt. Mocht er ondanks dat toch iets door glippen: zie hierboven.
