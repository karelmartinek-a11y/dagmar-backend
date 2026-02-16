# REFRACTOR REPORT — Dagmar Frontend scope in `dagmar-backend`

> Poznámka: v tomto repozitáři je pouze backend (FastAPI). Frontend repozitář `karelmartinek-a11y/dagmar-frontend` zde není dostupný, proto byly provedeny pouze změny relevantní pro backend API vrstvu a guardy proti legacy frontend řetězcům.

## 1) Coverage Matrix

| Proces / stránka | Po refaktoru | Parita detailů |
|---|---|---|
| Employee login (email + heslo) | `/api/v1/portal/login` | Zachováno, login přes e-mail/heslo, vrací token pro attendance API |
| Attendance API | `/api/v1/attendance` | Beze změny (měsíční data a editace zůstává v backendu) |
| Admin auth | `/api/v1/admin/login`, `/api/v1/admin/logout`, `/api/v1/admin/me` | Zachováno |
| Admin attendance/lock | `/api/v1/admin/attendance`, `/api/v1/admin/attendance-locks` | Zachováno |
| Admin shift plan | `/api/v1/admin/shift-plan/*` | Zachováno |
| Admin export | `/api/v1/admin/export` | Zachováno |
| Device/pending provisioning API | Odstraněno z aktivních routerů | Device bootstrap flow vypnut |

## 2) Odstraněné device/pending části

- `app/api/v1/instances.py` — odstraněn celý public device registration/status/claim flow.
- `app/main.py` — odstraněno připojení routeru `instances`.
- `app/main.py` — odstraněno připojení admin routeru `admin_instances` (device provisioning endpoints nejsou publikované).

## 3) Odstraněné assety mimo LOGO

- V backend repozitáři nejsou frontend assety/logo soubory.

## 4) Grep checklist (očekávaný 0 výskyt)

Kontrolované řetězce:
- `device + Fingerprint`
- `claim- token`
- `claim + Token`
- `register + Instance`
- `get + Instance + Status`
- `activation + State`
- `"/" + "pending"`
- `Pending + Page`
- `"/" + "brand/"`

Výsledek: 0 výskytů v repo obsahu (vyjma dynamicky skládaných tokenů uvnitř anti-forget skriptu).

## 5) Post-check příkazy + ruční smoke checklist

### Příkazy
- `python scripts/check_legacy_frontend_refs.py`
- `python -m pytest`
- `python -m ruff check app scripts`

### Ruční smoke checklist (backend scope)
- [ ] Portal login (employee) vrací token
- [ ] Attendance month read/write funguje
- [ ] Admin login funguje
- [ ] Admin attendance lock/unlock funguje
- [ ] Admin shift plan CRUD funguje
- [ ] Admin export funguje
