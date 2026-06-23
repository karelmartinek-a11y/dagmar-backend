# Endpointy a datové modely

## Přehled

Read-only integrační API je dostupné pod:

`https://dagmar.hcasc.cz/api/v1/integration`

Doporučené pořadí integrace:

1. `health`
2. `employments`
3. `shift-plan`
4. `attendances`
5. `punches`
6. `locks`

## Datové významy

### `employee_id`

`employee_id` odpovídá `PortalUser.id`, tedy identifikátoru osoby v docházkovém systému.

### `employment_id`

`employment_id` odpovídá `Employment.id`, tedy identifikátoru konkrétního úvazku.

Jeden zaměstnanec může mít více úvazků. Proto:

- `employee_id` identifikuje osobu
- `employment_id` identifikuje konkrétní pracovní vztah této osoby

### `record_id`

Aktuálně je `record_id` implementovaný na endpointu `shift-plan`. Jde o stabilní složený identifikátor ve formátu:

`YYYY-MM-DD:employment_id:shift_plan_id`

### Časy a timestampy

- datum: `YYYY-MM-DD`
- lokální čas v polích typu `arrival_time`, `departure_time`, `planned_arrival_time`, `event_time`: `HH:MM`
- UTC timestampy v polích typu `last_changed_at`, `locked_at`: ISO 8601 s `Z`
- timezone vracená datovými endpointy: `Europe/Prague`

API aktuálně nevrací pole `event_time_local`. Lokální čas průchodu je v poli `event_time`.

### Osobní údaje

API vrací omezený rozsah osobních údajů:

- `display_label` na `employments` obsahuje zobrazovaný popis úvazku včetně jména osoby
- `employee_id` a `employment_id`

API aktuálně nevrací e-mail, telefon ani jiné kontaktní údaje.

## `GET /health`

- scope: `integration:health`
- účel: základní ověření dostupnosti tokenu a integračního API
- query parametry: žádné

Úspěšná odpověď:

```json
{
  "ok": true,
  "service": "dagmar-integration-api",
  "api_version": "v1",
  "contract_version": "2026-06-22",
  "timezone": "Europe/Prague"
}
```

Typické chyby:

- `401 missing_token`
- `401 invalid_token`
- `403 client_disabled`
- `403 ip_forbidden`

## `GET /employments`

- scope: `employments:read`
- účel: seznam úvazků dostupných pro klienta

Query parametry:

| Parametr | Typ | Povinný | Výchozí | Poznámka |
| --- | --- | --- | --- | --- |
| `employment_id` | integer | ne | - | Filtr na konkrétní úvazek. |
| `employee_id` | integer | ne | - | Filtr na konkrétní osobu. |
| `active` | boolean | ne | - | Filtr podle aktivního stavu úvazku. |
| `date_from` | string `YYYY-MM-DD` | ne | - | Vrací jen úvazky, které do období zasahují. |
| `date_to` | string `YYYY-MM-DD` | ne | - | Vrací jen úvazky, které do období zasahují. |
| `limit` | integer | ne | `100` | Maximum `500`. |
| `cursor` | string | ne | - | Opaque cursor pro další stránku. |

Pole v `data[]`:

| Pole | Typ | Význam |
| --- | --- | --- |
| `employment_id` | integer | Identifikátor úvazku. |
| `employee_id` | integer | Identifikátor osoby. |
| `display_label` | string | Zobrazovaný název úvazku pro čtení partnerem. |
| `title` | string | Název pozice nebo úvazku. |
| `employment_type` | string | Typ úvazku, například `DPP_DPC`. |
| `start_date` | string | Začátek úvazku. |
| `end_date` | string nebo `null` | Konec úvazku. |
| `is_active` | boolean | Aktivní stav. |
| `last_changed_at` | string nebo `null` | Poslední známá změna v UTC. |
| `cursor_key` | integer | Interní stránkovací kotva. |

## `GET /shift-plan`

- scope: `shift_plan:read`
- účel: plán směn v období

Query parametry:

| Parametr | Typ | Povinný | Výchozí | Poznámka |
| --- | --- | --- | --- | --- |
| `date_from` | string `YYYY-MM-DD` | ano | - | Začátek období. |
| `date_to` | string `YYYY-MM-DD` | ano | - | Konec období. |
| `employment_id` | integer | ne | - | Filtr na úvazek. |
| `employee_id` | integer | ne | - | Filtr na osobu. |
| `include_locks` | boolean | ne | `false` | Přidá `lock_status`. |
| `limit` | integer | ne | `100` | Maximum `500`. |
| `cursor` | string | ne | - | Opaque cursor. |

Maximální délka období: 31 dnů.

Pole v `data[]`:

- `record_id`
- `shift_plan_id`
- `employment_id`
- `employee_id`
- `date`
- `planned_arrival_time`
- `planned_departure_time`
- `planned_status`
- `timezone`
- `lock_status` (`LOCKED` nebo `UNLOCKED`)
- `last_changed_at`
- `cursor_key`

## `GET /attendances`

- scope: `attendance:read`
- účel: denní docházkové záznamy

Query parametry:

| Parametr | Typ | Povinný | Výchozí | Poznámka |
| --- | --- | --- | --- | --- |
| `date_from` | string `YYYY-MM-DD` | ano | - | Začátek období. |
| `date_to` | string `YYYY-MM-DD` | ano | - | Konec období. |
| `employment_id` | integer | ne | - | Filtr na úvazek. |
| `employee_id` | integer | ne | - | Filtr na osobu. |
| `include_plan` | boolean | ne | `false` | Přidá vložený plán směny. |
| `include_locks` | boolean | ne | `false` | Přidá `lock_status`. |
| `include_punches` | boolean | ne | `false` | Přidá odvozené průchody. |
| `include_corrections` | boolean | ne | `false` | Aktuálně vrací `correction_status: "not_tracked"`. |
| `limit` | integer | ne | `100` | Maximum `500`. |
| `cursor` | string | ne | - | Opaque cursor. |

Maximální délka období: 31 dnů.

Pole v `data[]`:

- `attendance_id`
- `employment_id`
- `employee_id`
- `date`
- `arrival_time`
- `departure_time`
- `timezone`
- `plan` nebo `null`
- `lock_status`
- `punches` nebo `null`
- `correction_status` nebo `null`
- `last_changed_at`
- `cursor_key`

Poznámka:

- `punches` v tomto endpointu jsou odvozené z docházky
- `correction_status` je při `include_corrections=true` aktuálně vždy `not_tracked`

## `GET /punches`

- scope: `punches:read`
- účel: odvozené průchody z docházky

Query parametry:

| Parametr | Typ | Povinný | Výchozí | Poznámka |
| --- | --- | --- | --- | --- |
| `date_from` | string `YYYY-MM-DD` | ano | - | Začátek období. |
| `date_to` | string `YYYY-MM-DD` | ano | - | Konec období. |
| `employment_id` | integer | ne | - | Filtr na úvazek. |
| `employee_id` | integer | ne | - | Filtr na osobu. |
| `event_type` | string | ne | - | Pouze `ARRIVAL` nebo `DEPARTURE`. |
| `limit` | integer | ne | `100` | Maximum `500`. |
| `cursor` | string | ne | - | Opaque cursor. |

Maximální délka období: 31 dnů.

Pole v `data[]`:

- `attendance_id`
- `employment_id`
- `employee_id`
- `date`
- `source` vždy `derived_from_attendance`
- `raw_event_available` vždy `false`
- `event_type` (`ARRIVAL` nebo `DEPARTURE`)
- `event_time`
- `cursor_key`

Tento endpoint aktuálně nevrací raw terminálové eventy. Vrací pouze odvozené průchody z polí `attendance.arrival_time` a `attendance.departure_time`.

## `GET /locks`

- scope: `locks:read`
- účel: měsíční zámky docházky

Query parametry:

| Parametr | Typ | Povinný | Výchozí | Poznámka |
| --- | --- | --- | --- | --- |
| `year` | integer | podmíněně | - | Použijte spolu s `month`. |
| `month` | integer | podmíněně | - | Použijte spolu s `year`. |
| `date_from` | string `YYYY-MM-DD` | podmíněně | - | Alternativa k `year` + `month`. |
| `date_to` | string `YYYY-MM-DD` | podmíněně | - | Alternativa k `year` + `month`. |
| `employment_id` | integer | ne | - | Filtr na úvazek. |
| `employee_id` | integer | ne | - | Filtr na osobu. |
| `limit` | integer | ne | `100` | Maximum `500`. |
| `cursor` | string | ne | - | Opaque cursor. |

Je povinné zadat buď:

- `year` + `month`
- nebo `date_from` + `date_to`

Pole v `data[]`:

- `lock_id`
- `employment_id`
- `employee_id`
- `year`
- `month`
- `locked_at`
- `locked_by`
- `is_locked` vždy `true`
- `last_changed_at`
- `cursor_key`

## `GET /openapi.json`

- scope: `openapi:read`
- účel: strojově čitelný popis integračního API
- endpoint je chráněný a není veřejně vystaven bez tokenu

## Nepodporované funkce

### `GET /changes`

Endpoint `/api/v1/integration/changes` není implementovaný. Volání v integračním namespace skončí:

- `404 not_found`

Nepoužívejte jej jako podporovaný synchronizační mechanismus.
