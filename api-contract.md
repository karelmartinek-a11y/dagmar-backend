# DAGMAR – API Contract (Dagmar-Frontend ↔ Dagmar-Backend)

Verze: 2026-02-20  
Base path: `/api/v1`  
Kanonická doména: `https://dagmar.hcasc.cz`

Tento dokument je společný kontrakt mezi repozitáři **Dagmar-Frontend** a **Dagmar-Backend**.
Jakákoli změna endpointu nebo payloadu se musí promítnout do:
1) backend implementace, 2) frontend volání + typů, 3) tohoto kontraktu.

---

## 1) Konvence

- JSON requesty: `Content-Type: application/json; charset=utf-8`
- Čas: `"HH:MM"` (24h, nulované)
- Datum: `"YYYY-MM-DD"`

---

## 2) Admin session + CSRF

Admin používá session cookie a CSRF ochranu.

- CSRF cookie: `dagmar_csrf_token` (není HttpOnly – SPA ji může číst)
- CSRF header: `X-CSRF-Token: <token>`

Získání CSRF tokenu:
- `POST /api/v1/admin/login` vrací `csrf_token`
- `GET /api/v1/admin/csrf` vrací `csrf_token`

Všechny state-changing admin requesty (POST/PUT/DELETE) musí posílat `X-CSRF-Token`.

---

## 3) Healthcheck

Aktuální implementace má `GET /api/health`. Kontrakt požaduje i `GET /api/v1/health`.

### GET `/api/v1/health`
Response 200:
```json
{ "ok": true }
```

### GET `/api/health` (compat alias)
Response 200:
```json
{ "ok": true }
```

---

## 4) Public Instances

### POST `/api/v1/instances/register`
Request:
```json
{
  "client_type": "ANDROID",
  "device_fingerprint": "string",
  "device_info": { "any": "json" },
  "display_name": "string"
}
```
Response 200:
```json
{ "instance_id": "uuid", "status": "PENDING" }
```

### GET `/api/v1/instances/{instance_id}/status`
Response 200 (příklady):
```json
{ "status": "PENDING" }
```
```json
{ "status": "ACTIVE", "display_name": "string", "employment_template": "DPP_DPC", "afternoon_cutoff": "17:00" }
```

### POST `/api/v1/instances/{instance_id}/claim-token`
Response 200:
```json
{ "instance_token": "string", "display_name": "string" }
```

---

## 5) Portal auth (zaměstnanec)

### POST `/api/v1/portal/login`
Request:
```json
{ "email": "user@example.com", "password": "string" }
```
Response 200:
```json
{
  "instance_id": "uuid",
  "instance_token": "string",
  "display_name": "string",
  "employment_template": "DPP_DPC",
  "afternoon_cutoff": "17:00"
}
```

### POST `/api/v1/portal/reset`
Request:
```json
{ "token": "string", "password": "string" }
```
Response 200:
```json
{ "ok": true }
```

---

## 6) Attendance (zaměstnanec; Bearer token)

Header:
- `Authorization: Bearer <instance_token>`

### GET `/api/v1/attendance?year=YYYY&month=M`
Response 200:
```json
{
  "days": [
    {
      "date": "YYYY-MM-DD",
      "arrival_time": "08:00",
      "departure_time": "16:30",
      "planned_arrival_time": "08:00",
      "planned_departure_time": "16:00"
    }
  ],
  "instance_display_name": "string"
}
```

### PUT `/api/v1/attendance`
Request:
```json
{ "date": "YYYY-MM-DD", "arrival_time": "08:00", "departure_time": "16:30" }
```
Response 200:
```json
{ "ok": true }
```

Poznámka (audit): nevalidní `date` musí vracet HTTP 400 (ne 500).

---

## 7) Admin auth

### POST `/api/v1/admin/login`
Request:
```json
{ "username": "provoz@hotelchodovasc.cz", "password": "string" }
```
`username` je fixní administrátorská identita `provoz@hotelchodovasc.cz`.
Response 200:
```json
{ "ok": true, "csrf_token": "string" }
```

### GET `/api/v1/admin/csrf`
Response 200:
```json
{ "csrf_token": "string" }
```

### GET `/api/v1/admin/me`
Response 200:
```json
{ "authenticated": true, "username": "string" }
```
nebo:
```json
{ "authenticated": false }
```

### POST `/api/v1/admin/logout`
Response 200:
```json
{ "ok": true }
```

(compat) GET `/api/v1/admin/logout` → redirect na `/admin/login`

### POST `/api/v1/admin/forgot-password`
Request:
```json
{ "email": "provoz@hotelchodovasc.cz" }
```
Response 200:
```json
{ "ok": true }
```
Pozn.: endpoint posílá pouze nápovědný e-mail pro admin účet, nikdy reset token.


---

## 8) Admin – Instances (session; POST/DELETE vyžaduje CSRF)

### GET `/api/v1/admin/instances`
Response 200:
```json
[
  {
    "id": "uuid",
    "client_type": "WEB",
    "status": "ACTIVE",
    "display_name": "string",
    "profile_instance_id": null,
    "created_at": "2026-02-01T12:00:00Z",
    "last_seen_at": "2026-02-01T12:00:00Z",
    "activated_at": "2026-02-01T12:00:00Z",
    "revoked_at": null,
    "deactivated_at": null,
    "employment_template": "DPP_DPC",
    "afternoon_cutoff": "17:00"
  }
]
```

### POST `/api/v1/admin/instances/{instance_id}/activate`
### POST `/api/v1/admin/instances/{instance_id}/rename`
### POST `/api/v1/admin/instances/{instance_id}/set-template`
### POST `/api/v1/admin/instances/{instance_id}/revoke`
### POST `/api/v1/admin/instances/{instance_id}/deactivate`
### DELETE `/api/v1/admin/instances/{instance_id}`
### DELETE `/api/v1/admin/instances/pending`
### POST `/api/v1/admin/instances/merge`

Response: minimálně `{ "ok": true }` (dle implementace).

---

## 9) Admin – Users (session; POST/PUT vyžaduje CSRF)

Aktuální backend má:
- `GET /api/v1/admin/users`
- `POST /api/v1/admin/users`
- `POST /api/v1/admin/users/{id}/send-reset`

Audit požaduje doplnění update endpointu a telefonního čísla.

### GET `/api/v1/admin/users`
Response 200:
```json
{
  "users": [
    {
      "id": 1,
      "name": "string",
      "email": "string",
      "phone": "+420123456789",
      "role": "employee",
      "has_password": false,
      "profile_instance_id": "uuid-or-null",
      "is_active": true
    }
  ]
}
```

### POST `/api/v1/admin/users`
Request (aktuální):
```json
{ "name": "string", "email": "user@example.com", "role": "employee" }
```
Response 200:
```json
{ "id": 1, "name": "string", "email": "user@example.com", "role": "employee", "has_password": false }
```

### POST `/api/v1/admin/users/{user_id}/send-reset`
Response 200:
```json
{ "ok": true }
```

### PUT `/api/v1/admin/users/{user_id}`
Request:
```json
{
  "name": "string",
  "email": "user@example.com",
  "phone": "+420123456789",
  "role": "employee",
  "profile_instance_id": "uuid-or-null",
  "is_active": true
}
```
Response 200:
```json
{
  "id": 1,
  "name": "string",
  "email": "user@example.com",
  "phone": "+420123456789",
  "role": "employee",
  "has_password": true,
  "profile_instance_id": "uuid-or-null",
  "is_active": true
}
```

---

## 10) Admin – Attendance (session; PUT/POST vyžaduje CSRF)

### GET `/api/v1/admin/attendance?instance_id=...&year=YYYY&month=M`
Response 200:
```json
{ "days": [ { "date": "YYYY-MM-DD", "arrival_time": "08:00", "departure_time": "16:30" } ], "locked": false }
```

### PUT `/api/v1/admin/attendance`
Request:
```json
{ "instance_id": "uuid", "date": "YYYY-MM-DD", "arrival_time": "08:00", "departure_time": "16:30" }
```
Response 200:
```json
{ "ok": true }
```

### POST `/api/v1/admin/attendance/lock`
### POST `/api/v1/admin/attendance/unlock`
Request:
```json
{ "instance_id": "uuid", "year": 2026, "month": 2 }
```
Response 200:
```json
{ "ok": true }
```

---

## 11) Admin – Shift plan (session; PUT vyžaduje CSRF)

### GET `/api/v1/admin/shift-plan?year=YYYY&month=M`
Response 200: JSON (viz implementace).

### PUT `/api/v1/admin/shift-plan`
### PUT `/api/v1/admin/shift-plan/selection`
Request/Response: JSON (viz implementace).

---

## 12) Admin – Export

### GET `/api/v1/admin/export?...`
Response: stažení souboru (CSV/ZIP), nikoli JSON.

---

## 13) Admin – Settings / SMTP

### GET+PUT `/api/v1/admin/settings`
### GET+PUT `/api/v1/admin/smtp`
Pozn.: SMTP heslo se nesmí vracet v plaintextu v žádném GET response (aktuálně se vrací jen `password_set`).
