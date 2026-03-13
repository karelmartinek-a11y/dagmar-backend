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
- Referenční čas pro uživatelský zápis docházky: `Europe/Prague`

### GET `/api/v1/time`
Response 200:
```json
{ "datetime": "2026-03-13T10:24:00+01:00", "timezone": "Europe/Prague", "source": "server" }
```

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

Forenzní pravidla pro uživatele:
- Uživatel nesmí zapsat příchod ani odchod v budoucnosti vzhledem k času `Europe/Prague`.
- Web preferuje internetový čas; fallback je `/api/v1/time`; backend je finální autorita validace.
- Pro aktuální datum smí uživatel příchod i odchod měnit opakovaně.
- Pro předchozí dny v otevřeném měsíci smí uživatel pouze doplnit chybějící příchod nebo odchod.
- Již uložený příchod nebo odchod na minulém dni už uživatel nesmí měnit ani mazat.
- Tato omezení se nevztahují na admin endpointy.

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
- `PUT /api/v1/admin/users/{id}`
- `DELETE /api/v1/admin/users/{id}`

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
      "employment_template": "DPP_DPC",
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
{ "name": "string", "email": "user@example.com", "role": "employee", "employment_template": "DPP_DPC" }
```
Response 200:
```json
{ "id": 1, "name": "string", "email": "user@example.com", "role": "employee", "employment_template": "DPP_DPC", "has_password": false }
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
  "employment_template": "HPP",
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
  "employment_template": "HPP",
  "has_password": true,
  "profile_instance_id": "uuid-or-null",
  "is_active": true
}
```

### DELETE `/api/v1/admin/users/{user_id}`
Response 200:
```json
{ "ok": true }
```

Sémantika:
- smazání uživatele musí kaskádově smazat i jeho docházku
- admin není omezen forenzními pravidly platnými pro uživatele

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

## 14) Reminder e-maily ke směně

Tyto reminder e-maily se posílají na e-mail uložený u uživatele.

### Chybějící příchod
- Pokud má uživatel plánovaný příchod a 5 minut po plánovaném čase nemá zaznamenaný příchod, odešle se e-mail `Nemáš zapsaný příchod`.
- Odeslání se opakuje maximálně 5x, vždy po 10 minutách, dokud není příchod zapsán.

### Chybějící odchod ve 20:00
- Pokud má uživatel v daný den ve 20:00 zaznamenán pouze příchod bez odchodu, odešle se e-mail `JSI JEŠTĚ V PRÁCI? NEMÁŠ ZAPSÁN ODCHOD`.
- Odeslání se opakuje maximálně 5x, vždy po 15 minutách, dokud není odchod zapsán.
