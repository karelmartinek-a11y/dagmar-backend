# Autentizace a scopes

## Účel tokenu

Integrační API používá samostatný bearer token určený pro externí integrace s read endpointy a volitelně i se zápisem docházky podle přidělených scopes.

Tento token je oddělený od:

- admin session cookie + CSRF
- zaměstnaneckého bearer tokenu pro zaměstnaneckou část

Externí partner musí používat pouze integrační bearer token.

## Formát hlavičky

Požadavek musí posílat hlavičku:

```http
Authorization: Bearer dgi_REPLACE_WITH_TOKEN
```

Aktuální implementace očekává token s prefixem `dgi_`.

## Jak partner token získá

Token vydává správce Dagmar při vytvoření nebo rotaci integračního klienta.

- token se předává mimo API, bezpečným komunikačním kanálem
- token není možné získat z admin session
- token není možné odvodit z OpenAPI ani z veřejné dokumentace

## Co znamenají typické autentizační chyby

| Stav | `error.code` | Význam |
| --- | --- | --- |
| `401` | `missing_token` | Požadavek neposlal bearer token. |
| `401` | `invalid_token` | Token neodpovídá aktivnímu secretu. Stejně se projeví i starý token po rotaci nebo revokaci secretu. |
| `403` | `client_disabled` | Klient je zakázaný nebo expiroval. |
| `403` | `ip_forbidden` | Požadavek přišel z IP adresy mimo allowlist klienta. |

## IP allowlist

Aktuální implementace podporuje:

- jednotlivé IP adresy
- CIDR rozsahy

Pokud má klient allowlist prázdný, IP omezení se neuplatní. Pokud allowlist nastavený je, požadavek mimo povolený rozsah skončí `403 ip_forbidden`.

## Scopes

Aktuálně implementované scopes:

| Scope | Význam |
| --- | --- |
| `integration:health` | Přístup na `GET /health`. |
| `employments:read` | Čtení seznamu úvazků přes `GET /employments`. |
| `shift_plan:read` | Čtení plánu směn přes `GET /shift-plan`. |
| `attendance:read` | Čtení denní docházky přes `GET /attendances`. |
| `attendance:create` | Vytvoření docházky přes `POST /attendances`. |
| `attendance:update` | Částečná úprava docházky přes `PATCH /attendances/{attendance_id}`. |
| `attendance:delete` | Smazání docházky přes `DELETE /attendances/{attendance_id}`. |
| `punches:read` | Čtení odvozených průchodů přes `GET /punches`. |
| `locks:read` | Čtení měsíčních zámků přes `GET /locks`. |
| `openapi:read` | Přístup na chráněný `GET /openapi.json`. |

## Omezení datového rozsahu

Klient může být v implementaci omezen na:

- konkrétní `employment_id`
- konkrétní `employee_id`

Chování je dvojí:

- bez explicitního filtru API vrací jen záznamy spadající do povoleného rozsahu
- pokud klient explicitně požádá o `employment_id` nebo `employee_id` mimo svůj povolený rozsah, vrátí API `403 insufficient_scope`

## Chování při nedostatečném scope

Pokud klient nemá potřebný scope pro endpoint, API vrátí:

```json
{
  "error": {
    "code": "insufficient_scope",
    "message": "Klient nemá oprávnění pro tento endpoint.",
    "request_id": "..."
  }
}
```

## Bezpečnostní pravidla pro partnera

- používejte pouze HTTPS
- neposílejte token v URL, query stringu ani logovaných parametrech
- neukládejte token do klientských logů v plaintextu
- token nesdílejte mezi více systémy bez vědomí správce Dagmar
- po podezření na únik tokenu požádejte správce Dagmar o okamžitou rotaci
