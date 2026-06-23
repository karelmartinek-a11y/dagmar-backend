# Dokumentace integračního API Dagmar

Tato složka popisuje skutečně implementovaný stav read-only integračního API pod `/api/v1/integration`.

- Kanonická produkční doména: `https://dagmar.hcasc.cz`
- Produkční base URL integračního API: `https://dagmar.hcasc.cz/api/v1/integration`
- API je pouze pro čtení. Nepodporuje zápis ani úpravy docházky, plánů, zámků nebo zaměstnanců.
- Dokumentace je ověřená proti aktuálnímu zdrojovému kódu, migracím, testům a lokálně vygenerovanému OpenAPI výstupu integračního routeru.

## Veřejná partnerská část

Tyto soubory jsou určené pro externí technické partnery a neobsahují interní tajemství ani provozní údaje:

- [authentication.md](./authentication.md)
- [endpoints.md](./endpoints.md)
- [errors.md](./errors.md)
- [pagination-and-limits.md](./pagination-and-limits.md)
- [examples.md](./examples.md)
- [openapi.md](./openapi.md)
- [changelog.md](./changelog.md)

## Interní správcovská část

Tento soubor je určený jen pro správu integračních klientů a provoz:

- [admin-operations.md](./admin-operations.md)

## Shrnutí implementovaného rozsahu

Implementované endpointy:

- `GET /api/v1/integration/health`
- `GET /api/v1/integration/employments`
- `GET /api/v1/integration/shift-plan`
- `GET /api/v1/integration/attendances`
- `GET /api/v1/integration/punches`
- `GET /api/v1/integration/locks`
- `GET /api/v1/integration/openapi.json`

Nepodporované funkce v první verzi:

- zápis docházky
- úpravy plánů směn
- zamykání nebo odemykání přes integrační API
- správa zaměstnanců přes integrační API
- raw průchody z terminálu
- změnová synchronizace přes `/api/v1/integration/changes`
- použití admin session nebo zaměstnaneckého bearer tokenu místo integračního tokenu
