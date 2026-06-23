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
  "contract_version": "2026-06-22",
  "timezone": "Europe/Prague"
}
```

## 2. Úvazky

```bash
curl -sS \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  "https://dagmar.hcasc.cz/api/v1/integration/employments?active=true&limit=100"
```

```json
{
  "data": [
    {
      "employment_id": 101,
      "employee_id": 2201,
      "display_label": "Jan Partner - DPP/DPČ - Recepce",
      "title": "Recepce",
      "employment_type": "DPP_DPC",
      "start_date": "2026-06-01",
      "end_date": null,
      "is_active": true,
      "last_changed_at": "2026-06-22T22:28:47Z",
      "cursor_key": 101
    }
  ],
  "pagination": {
    "limit": 100,
    "next_cursor": null,
    "has_more": false
  }
}
```

## 3. Plán směn za krátké období

```bash
curl -sS \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  "https://dagmar.hcasc.cz/api/v1/integration/shift-plan?date_from=2026-06-10&date_to=2026-06-16&include_locks=true"
```

## 4. Docházka za krátké období

```bash
curl -sS \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  "https://dagmar.hcasc.cz/api/v1/integration/attendances?date_from=2026-06-10&date_to=2026-06-16&include_plan=true&include_locks=true&include_punches=true"
```

## 5. Odvozené průchody za krátké období

```bash
curl -sS \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  "https://dagmar.hcasc.cz/api/v1/integration/punches?date_from=2026-06-10&date_to=2026-06-16"
```

```json
{
  "data": [
    {
      "attendance_id": 8801,
      "employment_id": 101,
      "employee_id": 2201,
      "date": "2026-06-10",
      "source": "derived_from_attendance",
      "raw_event_available": false,
      "event_type": "ARRIVAL",
      "event_time": "08:03",
      "cursor_key": "2026-06-10:101:ARRIVAL:8801"
    }
  ],
  "pagination": {
    "limit": 100,
    "next_cursor": null,
    "has_more": false
  }
}
```

## 6. Zámky

```bash
curl -sS \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  "https://dagmar.hcasc.cz/api/v1/integration/locks?year=2026&month=6"
```

## 7. Stránkování s cursor

První stránka:

```bash
curl -sS \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  "https://dagmar.hcasc.cz/api/v1/integration/employments?limit=1"
```

Další stránka s opaque `cursor`:

```bash
curl -sS \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  "https://dagmar.hcasc.cz/api/v1/integration/employments?limit=1&cursor=eyJjdXJzb3Jfa2V5IjoxMDF9"
```

## 8. Typická chyba bez tokenu

```bash
curl -sS \
  https://dagmar.hcasc.cz/api/v1/integration/health
```

```json
{
  "error": {
    "code": "missing_token",
    "message": "Chybí přístupový token.",
    "request_id": "0f86d61ffe3d448d91981d8cb373e766"
  }
}
```

## 9. Typická chyba při překročení období

```bash
curl -sS \
  -H "Authorization: Bearer dgi_REPLACE_WITH_TOKEN" \
  "https://dagmar.hcasc.cz/api/v1/integration/attendances?date_from=2026-06-01&date_to=2026-07-15"
```

```json
{
  "error": {
    "code": "period_too_large",
    "message": "Požadované období je příliš velké.",
    "request_id": "7e579de5332842679effbbb2d026ff72"
  }
}
```
