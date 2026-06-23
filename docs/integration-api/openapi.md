# OpenAPI

## Umístění

OpenAPI výstup integračního API je dostupný na:

`GET https://dagmar.hcasc.cz/api/v1/integration/openapi.json`

## Ochrana endpointu

Endpoint není veřejný. Vyžaduje:

- integrační bearer token
- scope `openapi:read`

Bez tohoto scope partner OpenAPI JSON nezíská.

## Co OpenAPI obsahuje

Aktuální implementace generuje schema pouze z integračního routeru. OpenAPI tedy popisuje jen endpointy v namespace `/api/v1/integration`.

## Jak jej používat

Typický postup:

1. získat od správce Dagmar token se scope `openapi:read`
2. stáhnout chráněný JSON
3. importovat jej do interního klienta, codegen nástroje nebo API testera

## Důležitá implementační poznámka

OpenAPI je pomocný zdroj. Při integraci je vhodné řídit se také partnerskou dokumentací v této složce, protože runtime chování chybových obálek je v integračním namespace sjednocené a je přesnější než generické FastAPI defaulty.
