# KájovoDagmar docházkový systém — Backend (FastAPI)

Backend pro KájovoDagmar docházkový systém.

- Kanonická doména: **dagmar.hcasc.cz**
- API base path: **/api/v1/**
- Interní bind: **127.0.0.1:8101** (host-level)
- Databáze: PostgreSQL v Dockeru, publikovaná pouze na **127.0.0.1:5433**

> Pozor: Nikde nepoužívejte doménu `dochazka.hcasc.cz` – je zakázaná.

---

## 1) Co backend dělá

Backend implementuje:

- portal přihlášení zaměstnance (e-mail + heslo) a vydání bearer tokenu
- docházku (arrival/departure po dnech) s upsertem
- admin přihlášení přes **session cookie** + **CSRF** ochranu
- exporty:
  - CSV pro konkrétní instanci a měsíc
  - ZIP s více CSV pro všechny instance a měsíc
- rate limiting pro admin login a API provoz

---

## 2) Lokální spuštění (developer)

### 2.1 Požadavky

- Python 3.11+
- běžící PostgreSQL (pro dev můžete použít lokální Postgres; pro produkci viz server instrukce)

### 2.2 Vytvoření virtuálního prostředí

```bash
cd /opt/dagmar/backend
python3.11 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .
```

### 2.3 Konfigurace

Backend načítá konfiguraci z env proměnných (v produkci z `/etc/dagmar/backend.env`).

Pro lokální dev si můžete exportovat proměnné do shellu:

```bash
export DAGMAR_DATABASE_URL="postgresql+psycopg://dagmar:dagmar@127.0.0.1:5433/dagmar"
export DAGMAR_ADMIN_USERNAME="admin"
export DAGMAR_ADMIN_PASSWORD="change-me"
export DAGMAR_SESSION_SECRET="change-me-session-secret"
export DAGMAR_CSRF_SECRET="change-me-csrf-secret"
export DAGMAR_ALLOWED_ORIGINS="https://dagmar.hcasc.cz"
```

> `DAGMAR_ALLOWED_ORIGINS` se používá pro CORS (typicky jen vlastní doména v produkci).

### 2.4 Migrace DB

```bash
alembic upgrade head
```

### 2.5 Seed admin

Pro vytvoření admin účtu použijte skript v rootu projektu:

```bash
cd /opt/dagmar
./scripts/seed_admin.sh
```

### 2.6 Spuštění serveru

Pro dev (uvicorn):

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8101 --reload
```

Pro produkci se používá gunicorn (viz `gunicorn.conf.py`):

```bash
gunicorn -c gunicorn.conf.py app.main:app
```

---

## 3) Healthcheck

- `GET /api/v1/health`
  - vrací `{ "ok": true }` pokud aplikace běží a je dostupná DB

Příklad:

```bash
curl -sS http://127.0.0.1:8101/api/v1/health | jq
```

---

## 4) Bezpečnostní model

### 4.1 Zaměstnanec (portal login)

- zaměstnanec se přihlašuje přes portal endpoint (`/api/v1/portal/login`)
- po ověření e-mailu a hesla backend vydá bearer token pro attendance API

### 4.2 Admin

- `POST /api/v1/admin/login` nastaví session cookie
- pro admin akce je povinná validní session
- pro state-changing requesty je povinná **CSRF** ochrana

---

## 5) API přehled (odkaz)

Detailní kontrakt a příklady jsou v `../docs/API.md`.

Krátký seznam endpointů:

- Attendance:
  - `GET /api/v1/attendance?year=YYYY&month=MM`
  - `PUT /api/v1/attendance`

- Admin:
  - `POST /api/v1/admin/login`
  - `POST /api/v1/admin/logout`
  - `GET /api/v1/admin/me`
  - `GET /api/v1/admin/instances`
  - `POST /api/v1/admin/instances/{id}/activate`
  - `POST /api/v1/admin/instances/{id}/rename`
  - `POST /api/v1/admin/instances/{id}/revoke`
  - `GET /api/v1/admin/export?month=YYYY-MM&instance_id=...`
  - `GET /api/v1/admin/export?month=YYYY-MM&bulk=true`

---

## 6) Produkční poznámky

- Bind pouze na loopback `127.0.0.1:8101`
- Reverse proxy dělá Nginx (TLS terminace, security headers)
- Logy:
  - systemd journal: `journalctl -u dagmar-backend -f`
  - případně souborové logy do `/var/log/dagmar/` dle konfigurace služby

---

## 7) Časté problémy

1. **502 Bad Gateway v Nginx**
   - ověřte, že backend běží: `ss -lntp | grep 8101`
   - ověřte log: `journalctl -u dagmar-backend -n 200 --no-pager`

2. **Chyba DB připojení**
   - ověřte, že DAGMAR DB container běží a port je jen na loopbacku:
     - `docker ps`
     - `ss -lntp | grep 5433` (musí být `127.0.0.1:5433`)

3. **Admin login nefunguje (CSRF/session)**
   - ověřte, že používáte HTTPS a cookie má `Secure`
   - ověřte, že Nginx posílá správné `X-Forwarded-Proto https`
