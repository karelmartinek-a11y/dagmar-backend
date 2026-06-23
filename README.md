# KájovoDagmar docházkový systém — Backend (FastAPI)

> **Archivní repozitář — zákaz dalších změn**
>
> Tento repozitář `dagmar-backend` už není aktivním vývojovým ani deploy zdrojem pro `dagmar.hcasc.cz`.
> Aktivní repozitář je výhradně monorepo `karelmartinek-a11y/dagmar-monorepo`:
> `https://github.com/karelmartinek-a11y/dagmar-monorepo`
>
> V tomto repozitáři je zakázáno provádět jakékoli další změny kódu, konfigurace, workflow, dokumentace i deploye.
> Jakákoli změna provedená zde se nemá považovat za platnou změnu systému a nesmí se používat pro další vývoj.
Backend pro KĂˇjovoDagmar dochĂˇzkovĂ˝ systĂ©m.

- KanonickĂˇ domĂ©na: **dagmar.hcasc.cz**
- API base path: **/api/v1/**
- InternĂ­ bind: **127.0.0.1:8101** (host-level)
- DatabĂˇze: PostgreSQL v Dockeru, publikovanĂˇ pouze na **127.0.0.1:5433**

---

## 1) Co backend dÄ›lĂˇ

Backend implementuje:

- portal pĹ™ihlĂˇĹˇenĂ­ zamÄ›stnance (e-mail + heslo) a vydĂˇnĂ­ bearer tokenu
- dochĂˇzku (arrival/departure po dnech) s upsertem
- externĂ­ integraÄŤnĂ­ API pod `/api/v1/integration` s read endpointy a Ĺ™Ă­zenĂ˝m zĂˇpisem dochĂˇzky
- admin pĹ™ihlĂˇĹˇenĂ­ pĹ™es **session cookie** + **CSRF** ochranu
- exporty:
  - CSV pro konkrĂ©tnĂ­ instanci a mÄ›sĂ­c
  - ZIP s vĂ­ce CSV pro vĹˇechny instance a mÄ›sĂ­c
- rate limiting pro admin login a API provoz

---

## 2) LokĂˇlnĂ­ spuĹˇtÄ›nĂ­ (developer)

### 2.1 PoĹľadavky

- Python 3.11+
- bÄ›ĹľĂ­cĂ­ PostgreSQL (pro dev mĹŻĹľete pouĹľĂ­t lokĂˇlnĂ­ Postgres; pro produkci viz server instrukce)

### 2.2 VytvoĹ™enĂ­ virtuĂˇlnĂ­ho prostĹ™edĂ­

```bash
cd /opt/dagmar/backend
python3.11 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .
```

### 2.3 Konfigurace

Backend naÄŤĂ­tĂˇ konfiguraci z env promÄ›nnĂ˝ch (v produkci z `/etc/dagmar/backend.env`).

Pro lokĂˇlnĂ­ dev si mĹŻĹľete exportovat promÄ›nnĂ© do shellu:

```bash
export DAGMAR_DATABASE_URL="postgresql+psycopg://dagmar:dagmar@127.0.0.1:5433/dagmar"
export DAGMAR_ADMIN_PASSWORD="change-me"
export DAGMAR_SESSION_SECRET="change-me-session-secret"
export DAGMAR_CSRF_SECRET="change-me-csrf-secret"
export DAGMAR_CORS_ALLOW_ORIGINS="https://dagmar.hcasc.cz"
```

> `DAGMAR_CORS_ALLOW_ORIGINS` se pouĹľĂ­vĂˇ pro CORS (typicky jen vlastnĂ­ domĂ©na v produkci).

### 2.4 Migrace DB

```bash
alembic upgrade head
```

> PoznĂˇmka (PULS-009): Runtime DDL v request flow bylo odstranÄ›no. Pokud chybĂ­ tabulky pro shift-plan, backend je uĹľ za bÄ›hu nevytvĂˇĹ™Ă­; musĂ­ bĂ˝t pĹ™ipravenĂ© migracemi pĹ™ed startem aplikace.

### 2.5 Seed admin

Pro vytvoĹ™enĂ­ admin ĂşÄŤtu pouĹľijte skript v rootu projektu:

```bash
cd /opt/dagmar
./scripts/seed_admin.sh
```

### 2.6 SpuĹˇtÄ›nĂ­ serveru

Pro dev (uvicorn):

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8101 --reload
```

Pro produkci se pouĹľĂ­vĂˇ gunicorn (viz `gunicorn.conf.py`):

```bash
gunicorn -c gunicorn.conf.py app.main:app
```

---


### 2.7 Test bootstrap

```bash
pip install -e .[dev]
PYTHONPATH=. pytest
```

AlternativnÄ› pouĹľijte helper skript:

```bash
./scripts/test.sh
```

## 3) Healthcheck

- `GET /api/v1/health` (kanonickĂ˝ endpoint)
  - vracĂ­ `{ "ok": true }`.
- `GET /api/health` (kompatibilnĂ­ alias)
  - vracĂ­ stejnĂ© `{ "ok": true }`.

PĹ™Ă­klad:

```bash
curl -sS http://127.0.0.1:8101/api/v1/health | jq
```

---

## 4) BezpeÄŤnostnĂ­ model

### 4.1 ZamÄ›stnanec (portal login)

- zamÄ›stnanec se pĹ™ihlaĹˇuje pĹ™es portal endpoint (`/api/v1/portal/login`)
- po ovÄ›Ĺ™enĂ­ e-mailu a hesla backend vydĂˇ bearer token pro attendance API

### 4.2 Admin

- `POST /api/v1/admin/login` nastavĂ­ session cookie
- admin identita je pevnÄ› `provoz@hotelchodovasc.cz`
- pro admin akce je povinnĂˇ validnĂ­ session
- pro state-changing requesty je povinnĂˇ **CSRF** ochrana

---

## 5) API pĹ™ehled (odkaz)

DetailnĂ­ kontrakt a pĹ™Ă­klady jsou v `api-contract.md`.

KrĂˇtkĂ˝ seznam endpointĹŻ:

- Attendance:
  - `GET /api/v1/attendance?year=YYYY&month=MM`
  - `PUT /api/v1/attendance`

- Admin:
  - `POST /api/v1/admin/login`
  - `POST /api/v1/admin/logout`
  - `GET /api/v1/admin/me`
  - `GET /api/v1/admin/instances`
  - `GET /api/v1/admin/integrations/clients`
  - `POST /api/v1/admin/integrations/clients`
  - `POST /api/v1/admin/integrations/clients/{id}/rotate`
  - `POST /api/v1/admin/instances/{id}/activate`
  - `POST /api/v1/admin/instances/{id}/rename`
  - `POST /api/v1/admin/instances/{id}/revoke`
  - `GET /api/v1/admin/export?month=YYYY-MM&employment_id=...`
  - `GET /api/v1/admin/export?month=YYYY-MM&bulk=true`

- Integration:
  - `GET /api/v1/integration/health`
  - `GET /api/v1/integration/employments`
  - `GET /api/v1/integration/shift-plan`
  - `GET /api/v1/integration/attendances`
  - `GET /api/v1/integration/punches`
  - `GET /api/v1/integration/locks`
  - `GET /api/v1/integration/openapi.json`

## 5.1 IntegraÄŤnĂ­ API

- autentizace je samostatnĂ˝m bearer tokenem ve formĂˇtu `Authorization: Bearer dgi_<token>`
- integraÄŤnĂ­ tokeny jsou oddÄ›lenĂ© od zamÄ›stnaneckĂ˝ch `dg_` tokenĹŻ
- `/api/v1/integration/punches` vracĂ­ pouze **odvozenĂ© prĹŻchody** z `attendance.arrival_time` a `attendance.departure_time`
- `/api/v1/integration/changes` v tĂ©to etapÄ› neexistuje, protoĹľe backend zatĂ­m nemĂˇ spolehlivĂ˝ change log
- list endpointy vracĂ­ `data` a `pagination`
- `shift-plan`, `attendances` a `punches` vyĹľadujĂ­ `date_from` a `date_to`, maximĂˇlnĂ­ obdobĂ­ je 31 dnĂ­
- detailnĂ­ partnerskĂˇ a internĂ­ sprĂˇvcovskĂˇ dokumentace je v `docs/integration-api/`

## 5.2 ProvoznĂ­ sprĂˇva integraÄŤnĂ­ch klientĹŻ

IntegraÄŤnĂ­ klienty lze spravovat dvÄ›ma cestami:

- produkÄŤnĂ­ admin sekcĂ­ `https://dagmar.hcasc.cz/admin/integrace`
- fallback skriptem:

```bash
python scripts/manage_integrations.py list
python scripts/manage_integrations.py create --name "mzdovy-import" --scopes "integration:health,employments:read"
python scripts/manage_integrations.py rotate 1
python scripts/manage_integrations.py disable 1
python scripts/manage_integrations.py enable 1
python scripts/manage_integrations.py revoke 1
```

Plaintext integraÄŤnĂ­ token se zobrazuje pouze pĹ™i vytvoĹ™enĂ­ nebo rotaci. Do databĂˇze se uklĂˇdĂˇ jen hash, fingerprint a `last4`.

---

## 6) ProdukÄŤnĂ­ poznĂˇmky

- Bind pouze na loopback `127.0.0.1:8101`
- Reverse proxy dÄ›lĂˇ Nginx (TLS terminace, security headers)
- Logy:
  - systemd journal: `journalctl -u dagmar-backend -f`
  - pĹ™Ă­padnÄ› souborovĂ© logy do `/var/log/dagmar/` dle konfigurace sluĹľby

---

## 7) ÄŚastĂ© problĂ©my

1. **502 Bad Gateway v Nginx**
   - ovÄ›Ĺ™te, Ĺľe backend bÄ›ĹľĂ­: `ss -lntp | grep 8101`
   - ovÄ›Ĺ™te log: `journalctl -u dagmar-backend -n 200 --no-pager`

2. **Chyba DB pĹ™ipojenĂ­**
   - ovÄ›Ĺ™te, Ĺľe DAGMAR DB container bÄ›ĹľĂ­ a port je jen na loopbacku:
     - `docker ps`
     - `ss -lntp | grep 5433` (musĂ­ bĂ˝t `127.0.0.1:5433`)

3. **Admin login nefunguje (CSRF/session)**
   - ovÄ›Ĺ™te, Ĺľe pouĹľĂ­vĂˇte HTTPS a cookie mĂˇ `Secure`
   - ovÄ›Ĺ™te, Ĺľe Nginx posĂ­lĂˇ sprĂˇvnĂ© `X-Forwarded-Proto https`
