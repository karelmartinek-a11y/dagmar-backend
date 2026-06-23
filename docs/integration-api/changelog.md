# Changelog dokumentace

## Verze dokumentace 2026-06-23

- datum ověření: `2026-06-23`
- API verze: `v1`
- contract version vracená endpointem `health`: `2026-06-22`
- ověřený backend commit: `8e0c2485b2c1d58f7af4c25b6d59b24f31ba5a0d`
- ověřený frontend commit pro veřejnou stránku před úpravou: `fbbc569b0ae1fbf815eb6a938c152c2f2ae786ec`

## Obsah této verze

- zdokumentované skutečně implementované endpointy `health`, `employments`, `shift-plan`, `attendances`, `punches`, `locks`, `openapi.json`
- výslovně uvedeno, že `/changes` není implementovaný endpoint
- výslovně uvedeno, že `punches` vrací odvozené průchody z denní docházky a ne raw terminálové eventy
- doplněna veřejná partnerská vrstva a oddělená interní správcovská vrstva
