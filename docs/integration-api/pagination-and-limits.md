# Stránkování a limity

## Společný tvar stránkování

List endpointy vrací:

```json
{
  "data": [],
  "pagination": {
    "limit": 100,
    "next_cursor": null,
    "has_more": false
  }
}
```

Význam polí:

- `limit`: skutečně použitý limit stránky
- `next_cursor`: opaque hodnota pro další stránku nebo `null`
- `has_more`: `true`, pokud existují další záznamy

## Parametry stránkování

- `limit`
  - výchozí hodnota: `100`
  - maximální hodnota: `500`
  - hodnoty menší než `1` končí `400 invalid_request`
- `cursor`
  - opaque hodnota vrácená z předchozí stránky
  - klient ji má vracet beze změny
  - neplatný cursor končí `400 invalid_request`

## Jak iterovat přes stránky

1. proveďte první dotaz bez `cursor`
2. zpracujte `data`
3. pokud je `pagination.has_more = true` a `pagination.next_cursor` není `null`, opakujte stejný dotaz s `cursor=<next_cursor>`
4. skončete, když `has_more = false`

## Poznámka k `cursor_key`

Aktuální implementace vrací v jednotlivých záznamech i pole `cursor_key`.

- partner ho může použít jen pro diagnostiku
- nejde o obchodní identifikátor
- pro stránkování je závazný pouze `pagination.next_cursor`

## Limity období podle endpointu

### Endpointy s pevným maximem 31 dnů

Tyto endpointy vyžadují `date_from` a `date_to` a povolují maximálně 31 kalendářních dnů včetně:

- `GET /shift-plan`
- `GET /attendances`
- `GET /punches`

Při překročení limitu vrací API:

```json
{
  "error": {
    "code": "period_too_large",
    "message": "Požadované období je příliš velké.",
    "request_id": "..."
  }
}
```

### `GET /employments`

`date_from` a `date_to` jsou zde volitelné překryvové filtry nad úvazkem. Aktuální implementace na tomto endpointu nevynucuje maximální délku období.

### `GET /locks`

Endpoint vyžaduje buď:

- `year` + `month`
- nebo `date_from` + `date_to`

Aktuální implementace zde nevynucuje 31denní limit. Při použití `date_from` a `date_to` filtruje podle měsíců spadajících do zadaného intervalu.

## Doporučení pro dávkové stahování

- `shift-plan`, `attendances` a `punches` stahujte po intervalech nejvýše 31 dnů
- pro pravidelnou synchronizaci preferujte kratší dávky, například po dnech nebo týdnech
- `locks` stahujte přirozeně po měsících

## Rate limiting

Aktuálně implementované limity jsou pevně zabudované v routeru:

- `GET /health`: 60 požadavků za minutu
- datové endpointy `employments`, `shift-plan`, `attendances`, `punches`, `locks`: 120 požadavků za minutu
- `GET /openapi.json`: 10 požadavků za minutu

Při překročení limitu API vrací:

```json
{
  "error": {
    "code": "rate_limited",
    "message": "Byl překročen limit požadavků.",
    "request_id": "..."
  }
}
```

Doporučené chování klienta:

- omezte paralelismus
- při `429` použijte exponenciální backoff
- opakované pokusy nerozjíždějte bez omezení
