# Interní správa integračních klientů

Tento soubor je interní provozní dokumentace. Není určený pro veřejné vystavení bez dalšího očištění.

## Kde se klienti spravují

Primární cesta:

- admin web `https://dagmar.hcasc.cz/admin/integrace`

Fallback:

- CLI skript `python scripts/manage_integrations.py`

## Co umí admin web

Admin sekce `/admin/integrace` aktuálně podporuje:

- výpis klientů
- vytvoření klienta
- nastavení scope
- omezení na `allowed_employment_ids`
- omezení na `allowed_employee_ids`
- IP allowlist
- expiraci `expires_at`
- zobrazení fingerprintu a `last4`
- zobrazení plaintext tokenu pouze jednou po vytvoření nebo rotaci
- rotaci tokenu
- zakázání klienta
- opětovné povolení klienta
- revokaci aktivního secretu

## Vytvoření klienta v admin UI

1. Přihlaste se do adminu.
2. Otevřete `/admin/integrace`.
3. Vyplňte název klienta.
4. Vyplňte scopes jako čárkou oddělený seznam.
5. Volitelně vyplňte povolené `employment_id`.
6. Volitelně vyplňte povolené `employee_id`.
7. Volitelně vyplňte IP allowlist.
8. Volitelně nastavte `expires_at` v ISO 8601.
9. Odešlete formulář.
10. Zobrazený plaintext token bezpečně předejte partnerovi. Později už se v UI znovu neukáže.

## Rotace tokenu

Rotace v implementaci znamená:

- všechny dosud aktivní secrety klienta dostanou `revoked_at` a `rotated_at`
- vygeneruje se nový plaintext token
- partner musí začít používat nový token
- starý token se pak na API projeví jako `401 invalid_token`

## Zakázání klienta

Akce `Zakázat` mění status klienta na `DISABLED`.

Chování v API:

- požadavky s jinak platným tokenem vracejí `403 client_disabled`

## Povolení klienta

Akce `Povolit` vrací status klienta na `ACTIVE`.

Aktuální backend umí při povolení současně změnit:

- scopes
- `allowed_employment_ids`
- `allowed_employee_ids`
- `ip_allowlist`
- `expires_at`

## Revokace secretu

Akce `Revokovat secret`:

- nastaví `revoked_at` na všech aktivních secretech klienta
- přepne klienta do stavu `REVOKED`

Chování v API:

- dosavadní token se projeví jako `401 invalid_token`

## IP allowlist

Povolené jsou:

- jednotlivé IP
- CIDR rozsahy

Příklad:

- `203.0.113.10`
- `198.51.100.0/24`

## Audit log

Integrační požadavky se zapisují do audit logu s těmito typy údajů:

- `request_id`
- čas požadavku
- HTTP metoda
- cesta
- hash query stringu
- zdrojová IP
- user-agent
- status kód
- error code
- počet vrácených řádků
- doba zpracování

Externímu partnerovi se nemají předávat názvy interních tabulek ani interní dotazy. Pro provozní ověření ale počítejte s tím, že audit log existuje a je ukládán na backendu.

## CLI fallback

Bezpečné příklady použití:

```bash
python scripts/manage_integrations.py list
python scripts/manage_integrations.py create --name "partner-mzdy" --scopes "integration:health,employments:read,attendance:read"
python scripts/manage_integrations.py rotate 1
python scripts/manage_integrations.py disable 1
python scripts/manage_integrations.py enable 1
python scripts/manage_integrations.py revoke 1
```

Pozor:

- výstup `create` a `rotate` vypíše plaintext token
- plaintext token nekopírujte do issue trackeru, wiki, ticketu ani commitu
- po předání partnerovi uložte pouze fingerprint a `last4`, ne plaintext

## Bezpečné předání tokenu třetí osobě

- používejte jednorázový zabezpečený kanál
- nesdílejte token v běžném e-mailovém vlákně bez šifrování
- nesdílejte screenshot tokenu ve skupinových chatech
- partnerovi předejte i informaci o scope, povoleném rozsahu a IP allowlistu
