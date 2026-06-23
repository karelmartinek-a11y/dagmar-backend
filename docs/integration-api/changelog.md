# Changelog dokumentace

## Verze dokumentace 2026-06-23

- datum ověření: `2026-06-23`
- API verze: `v1`
- contract version vracená endpointem `health`: `2026-06-23`
- dokumentace byla rozšířena z čistě read-only režimu na řízený read/write režim pro docházku

## Obsah této verze

- zdokumentované read endpointy `health`, `employments`, `shift-plan`, `attendances`, `punches`, `locks`, `openapi.json`
- zdokumentované write endpointy `POST /attendances`, `PATCH /attendances/{attendance_id}`, `DELETE /attendances/{attendance_id}`
- popsané nové scopes `attendance:create`, `attendance:update`, `attendance:delete`
- výslovně uvedeno, že write se týká pouze docházky a ne správy zaměstnanců, úvazků, plánů služeb nebo zámků
- doplněné konflikty, zamčená období, audit a optimistic concurrency přes `expected_updated_at`
