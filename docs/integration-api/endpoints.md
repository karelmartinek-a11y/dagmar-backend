# Endpointy a datové modely

## Přehled

Integrační API je dostupné pod:

`https://dagmar.hcasc.cz/api/v1/integration`

Doporučené pořadí integrace:

1. `health`
2. `employments`
3. `shift-plan`
4. `attendances`
5. `punches`
6. `locks`
7. write operace pro docházku

## Datové významy

- `employee_id` je identifikátor osoby v systému Dagmar.
- `employment_id` je identifikátor konkrétního úvazku.
- invariant docházky je `employment_id + date`
- lokální časy jsou ve formátu `HH:MM`
- UTC timestampy jsou ve formátu ISO 8601 s `Z`

## Read endpointy

### `GET /health`

- scope: `integration:health`
- účel: ověření tokenu a dostupnosti API

Úspěšná odpověď:

```json
{
  "ok": true,
  "service": "dagmar-integration-api",
  "api_version": "v1",
  "contract_version": "2026-06-23",
  "timezone": "Europe/Prague"
}
```

### `GET /employments`

- scope: `employments:read`
- vrací seznam úvazků dostupných pro klienta

### `GET /shift-plan`

- scope: `shift_plan:read`
- vrací plán směn v období
- maximální období: 31 dnů

### `GET /attendances`

- scope: `attendance:read`
- vrací denní docházku
- maximální období: 31 dnů
- zapisovatelná doménová pole v aktuálním modelu jsou pouze `arrival_time` a `departure_time`

### `GET /punches`

- scope: `punches:read`
- vrací pouze odvozené průchody `ARRIVAL` a `DEPARTURE`
- nejde o raw terminálové eventy

### `GET /locks`

- scope: `locks:read`
- vrací existující měsíční zámky docházky

### `GET /openapi.json`

- scope: `openapi:read`
- vrací chráněné OpenAPI schéma integračního routeru

## Write endpointy pro docházku

### `POST /attendances`

- scope: `attendance:create`
- vytváří nový docházkový záznam pro existující `employment_id` a `date`
- pokud docházka pro stejný klíč už existuje, vrací `409 duplicate_attendance`
- nesmí vytvořit uživatele, zaměstnance ani úvazek

Request body:

```json
{
  "employment_id": 101,
  "date": "2026-06-12",
  "arrival_time": "08:00",
  "departure_time": "16:30"
}
```

### `PATCH /attendances/{attendance_id}`

- scope: `attendance:update`
- částečně upravuje existující docházku
- přijímá pouze `arrival_time`, `departure_time` a volitelné `expected_updated_at`
- technická pole jako `id`, `created_at`, `updated_at` nebo `instance_id` nejsou přímo zapisovatelná

### `DELETE /attendances/{attendance_id}`

- scope: `attendance:delete`
- smaže existující docházku
- volitelně přijímá `expected_updated_at`
- mazání je auditované a respektuje datový rozsah i zámky

## Zámky a konflikty

- write operace nikdy neobcházejí zamčené měsíce
- zamčené období vrací `409 attendance_locked`
- zastaralé `expected_updated_at` vrací `409 conflict`
- write operace respektují stejný datový rozsah jako read endpointy

## Nepodporované funkce

- `PUT /attendances`
- `/changes`
- write operace mimo docházku
- správa uživatelů, zaměstnanců, úvazků, plánů služeb a zámků
