# Dokumentace integračního API Dagmar

Tato složka popisuje skutečně implementovaný stav integračního API pod `/api/v1/integration`.

- Kanonická produkční doména: `https://dagmar.hcasc.cz`
- Produkční base URL integračního API: `https://dagmar.hcasc.cz/api/v1/integration`
- Contract version vracená endpointem `health`: `2026-06-23`
- Dokumentace je ověřená proti aktuálnímu zdrojovému kódu, migracím, testům a veřejné stránce `https://dagmar.hcasc.cz/integration-api`

## Rozsah API

API umí:

- číst úvazky, plán směn, docházku, odvozené průchody a zámky
- vytvářet docházku
- částečně upravovat docházku
- mazat docházku

API neumí:

- spravovat uživatele, zaměstnance ani úvazky
- měnit plán služeb
- zamykat nebo odemykat období
- obcházet zamčené měsíce
- zapisovat technická pole docházky
- používat admin session nebo zaměstnanecký bearer token místo integračního `dgi_` tokenu

## Veřejná partnerská část

- [authentication.md](./authentication.md)
- [endpoints.md](./endpoints.md)
- [errors.md](./errors.md)
- [pagination-and-limits.md](./pagination-and-limits.md)
- [examples.md](./examples.md)
- [openapi.md](./openapi.md)
- [changelog.md](./changelog.md)

## Interní správcovská část

- [admin-operations.md](./admin-operations.md)

## Implementované endpointy

- `GET /api/v1/integration/health`
- `GET /api/v1/integration/employments`
- `GET /api/v1/integration/shift-plan`
- `GET /api/v1/integration/attendances`
- `POST /api/v1/integration/attendances`
- `PATCH /api/v1/integration/attendances/{attendance_id}`
- `DELETE /api/v1/integration/attendances/{attendance_id}`
- `GET /api/v1/integration/punches`
- `GET /api/v1/integration/locks`
- `GET /api/v1/integration/openapi.json`
