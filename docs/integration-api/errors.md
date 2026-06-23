# Chybové odpovědi

## Jednotný tvar chyby

Integrační endpointy vrací chyby ve společné obálce:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "Neplatný požadavek.",
    "request_id": "c8e893678962440b89d7a172c3a4efa2"
  }
}
```

Součástí odpovědi je také hlavička `X-Request-ID` se stejnou hodnotou.

## Přehled status kódů

| HTTP | `error.code` | Kdy vzniká |
| --- | --- | --- |
| `400` | `invalid_request` | Chybějící nebo neplatné parametry, neplatný cursor, chybné kombinace parametrů. |
| `400` | `period_too_large` | Požadované období přesáhne 31 dnů na `shift-plan`, `attendances` nebo `punches`. |
| `401` | `missing_token` | Chybí bearer token. |
| `401` | `invalid_token` | Token neexistuje, je ve špatném formátu nebo už neodpovídá aktivnímu secretu. |
| `403` | `client_disabled` | Klient je zakázaný nebo expiroval. |
| `403` | `ip_forbidden` | IP adresa není v povoleném allowlistu klienta. |
| `403` | `insufficient_scope` | Chybí scope nebo je požadovaný rozsah mimo povolené zaměstnance/úvazky. |
| `404` | `not_found` | Neexistující endpoint v integračním namespace, například `/changes`. |
| `429` | `rate_limited` | Překročen limit požadavků. |
| `500` | `internal_error` | Nezpracovaná interní chyba. |

## Praktické příklady

### Chybějící token

```json
{
  "error": {
    "code": "missing_token",
    "message": "Chybí přístupový token.",
    "request_id": "0f86d61ffe3d448d91981d8cb373e766"
  }
}
```

### Neplatný token

```json
{
  "error": {
    "code": "invalid_token",
    "message": "Přístupový token není platný.",
    "request_id": "9bfb91a7af8741dbba6a3249a8fa69cc"
  }
}
```

### Klient mimo IP allowlist

```json
{
  "error": {
    "code": "ip_forbidden",
    "message": "Požadavek není povolen z této IP adresy.",
    "request_id": "6d9ae61922804abe85a41941cb94d05c"
  }
}
```

### Nedostatečný scope

```json
{
  "error": {
    "code": "insufficient_scope",
    "message": "Klient nemá oprávnění pro tento endpoint.",
    "request_id": "edbac254f8754d04a449bbff15ef656b"
  }
}
```

### Příliš velké období

```json
{
  "error": {
    "code": "period_too_large",
    "message": "Požadované období je příliš velké.",
    "request_id": "7e579de5332842679effbbb2d026ff72"
  }
}
```

### Chybný výběr parametrů pro zámky

```json
{
  "error": {
    "code": "invalid_request",
    "message": "U zámků zadejte year+month nebo date_from+date_to.",
    "request_id": "ea6e7845fd9b437e9afe32f544e586ab"
  }
}
```

### Nepodporovaný endpoint `/changes`

```json
{
  "error": {
    "code": "not_found",
    "message": "Požadovaný zdroj nebyl nalezen.",
    "request_id": "02b2465668be4a42b9b5489601506813"
  }
}
```

## Poznámka k validačním chybám

Generované OpenAPI schema může u některých parametrů uvádět standardní FastAPI `422 Validation Error`, ale aktuální runtime obsluha integračního namespace normalizuje neplatné požadavky na `400 invalid_request`.
