# Příklady volání

Všechny příklady používají pouze kanonickou doménu `https://dagmar.hcasc.cz` a zástupný token `dgi_REPLACE_WITH_TOKEN`.

## 1. Health check

```bash
curl -sS \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  https://dagmar.hcasc.cz/api/v1/integration/health
```

```json
{
  "ok": true,
  "service": "dagmar-integration-api",
  "api_version": "v1",
  "contract_version": "2026-06-23",
  "timezone": "Europe/Prague"
}
```

## 2. Čtení docházky

```bash
curl -sS \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  "https://dagmar.hcasc.cz/api/v1/integration/attendances?date_from=2026-06-10&date_to=2026-06-16&include_plan=true&include_locks=true&include_punches=true"
```

## 3. Vytvoření docházky

```bash
curl -sS \
  -X POST \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "employment_id": 101,
    "date": "2026-06-12",
    "arrival_time": "08:00",
    "departure_time": "16:30"
  }' \
  https://dagmar.hcasc.cz/api/v1/integration/attendances
```

## 4. Úprava docházky

```bash
curl -sS \
  -X PATCH \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "departure_time": "16:45",
    "expected_updated_at": "2026-06-23T09:14:00Z"
  }' \
  https://dagmar.hcasc.cz/api/v1/integration/attendances/501
```

## 5. Mazání docházky

```bash
curl -sS \
  -X DELETE \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "expected_updated_at": "2026-06-23T09:14:00Z"
  }' \
  https://dagmar.hcasc.cz/api/v1/integration/attendances/501
```

## 6. Typická chyba při duplicitním vytvoření

```json
{
  "error": {
    "code": "duplicate_attendance",
    "message": "Docházka pro zadaný úvazek a datum už existuje.",
    "request_id": "7e579de5332842679effbbb2d026ff72"
  }
}
```

## 7. Typická chyba při zamčeném období

```json
{
  "error": {
    "code": "attendance_locked",
    "message": "Docházka za zvolené období je uzamčena.",
    "request_id": "0f86d61ffe3d448d91981d8cb373e766"
  }
}
```
