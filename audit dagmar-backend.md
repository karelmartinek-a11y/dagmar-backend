# Forenzní audit DAGMAR backendu (repo-only)

Rozsah: audit je proveden **výhradně** nad obsahem tohoto repozitáře (bez externího webu, bez runtime přístupu na dagmar.hcasc.cz).

## Metodika
- statická kontrola všech sledovaných souborů v repu,
- kontrola kontraktů v README vs. implementace endpointů,
- hledání nedodělků, slepých funkcí, placeholderů a bezpečnostních rizik,
- ověření lint/test základů.

## PULS tabulka

| ID | P (Problém) | U (Urgence) | L (Lokalita) | S (Stav / doporučení) |
|---|---|---|---|---|
| PULS-001 | **Nesoulad health endpointu**: README deklaruje `GET /api/v1/health`, implementace má `GET /api/health`. | Vysoká | `README.md`, `app/main.py` | Sjednotit kontrakt (preferovat `/api/v1/health` nebo aktualizovat README + klienty). |
| PULS-002 | **Slepá (nepřipojená) admin funkcionalita instancí**: existuje modul `admin_instances.py`, ale router není připojen v `main.py`. | Kritická | `app/api/v1/admin_instances.py`, `app/main.py` | Připojit router v `create_app()` nebo modul odstranit; nyní jsou endpointy funkčně mrtvé. |
| PULS-003 | **Chybná obsluha nevalidního data v employee attendance upsertu**: při špatném datu se vyhazuje `ValueError`, což skončí generickým 500 místo 4xx. | Vysoká | `app/api/v1/attendance.py` | Převést na `HTTPException(status_code=400, ...)` konzistentně s ostatními endpointy. |
| PULS-004 | **Neodpovídá požadovanému admin modelu „natvrdo e-mail“**: admin login je přes `admin_username` z env, nikoli fixně `provoz@hotelchodovasc.cz`. | Vysoká | `app/config.py`, `app/api/v1/admin_auth.py` | Zavést jednoznačný admin identifikátor dle zadání (hardcoded/konfiguračně zamčený e-mail) + migrace UX a dokumentace. |
| PULS-005 | **Chybí flow „zapomenuté heslo admina = nápovědný e-mail“**: v API není samostatný endpoint/proces pro admin help-mail bez token resetu. | Vysoká | `app/api/v1/admin_auth.py`, `app/api/v1/admin_users.py` | Doplnit explicitní admin-forgot-password workflow oddělený od user reset tokenů. |
| PULS-006 | **Správa uživatelů neumí editaci jméno/e-mail/telefon dle požadavku**: je pouze list/create/send-reset; chybí update endpoint a v modelu není telefon. | Kritická | `app/api/v1/admin_users.py`, `app/db/models.py` | Přidat `phone` do `PortalUser` + CRUD update endpoint + validační pravidla. |
| PULS-007 | **Dead code / neaktivní webhook modul**: `webhook_whatsapp.py` existuje, ale není připojen routerem do app. | Střední | `app/api/webhook_whatsapp.py`, `app/main.py` | Rozhodnout: buď připojit a zabezpečit, nebo odstranit jako nevyužitý modul. |
| PULS-008 | **Riziko ukládání SMTP hesla v plaintextu v DB** (`smtp_password` přímo text). | Vysoká | `app/db/models.py`, `app/api/v1/admin_smtp.py` | Minimálně šifrovat at-rest (KMS/secret envelope), maskovat přístupy a audit log. |
| PULS-009 | **Runtime DDL v request flow**: `_ensure_shift_plan_tables()` vytváří tabulky během requestu. | Střední | `app/api/v1/attendance.py`, `app/api/v1/admin_shift_plan.py` | Přesunout výhradně do migrací; runtime create je křehký a může způsobovat latenci/race pod zátěží. |
| PULS-010 | **Repo obsahuje přebytečné `.DIFF` artefakty** (historické patch soubory), které zvyšují šum a riziko záměny SSOT. | Nízká | root: `BackDagmar2.DIFF`, `BackDagmar_Q.DIFF`, `DAGBAC.DIFF` | Přesunout do archivní složky nebo odstranit z produkčního repa. |
| PULS-011 | **Nulové automatické testy v repu** (`pytest` nenalezl testy). | Vysoká | celý repo | Přidat minimálně smoke testy kritických auth/attendance toků a kontrakt testy API. |
| PULS-012 | **UI ergonomii nelze auditovat**: tento repo je backend-only, frontend zde není. | Informační | `docs/REFRACTOR_REPORT_Dagmar-Frontend.md` | Pro UI audit dodat frontend repo/build artefakty; bez nich nelze ověřit ergonomii, skeletony ani nefunkční obrazovky. |

## Doporučené pořadí nápravy (top 5)
1. PULS-002 (zprovoznění nebo odstranění mrtvých admin endpointů instancí).
2. PULS-006 (doplnění uživatelského modelu + editace v admin správě).
3. PULS-003 (oprava chybových kódů attendance upsert).
4. PULS-004 + PULS-005 (sjednocení admin identity + forgot-password/help policy).
5. PULS-011 (zavedení automatických testů jako gate pro regresi).

