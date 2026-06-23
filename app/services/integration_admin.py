from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import models
from app.services.employment_access import employment_label

NAME_MIN_LENGTH = 3
NAME_MAX_LENGTH = 80

SCOPE_HEALTH = "integration:health"
SCOPE_EMPLOYMENTS = "employments:read"
SCOPE_SHIFT_PLAN = "shift_plan:read"
SCOPE_ATTENDANCE = "attendance:read"
SCOPE_ATTENDANCE_CREATE = "attendance:create"
SCOPE_ATTENDANCE_UPDATE = "attendance:update"
SCOPE_ATTENDANCE_DELETE = "attendance:delete"
SCOPE_PUNCHES = "punches:read"
SCOPE_LOCKS = "locks:read"
SCOPE_OPENAPI = "openapi:read"
SCOPE_CHANGES = "changes:read"

DATA_SCOPE_ALL = "ALL_EMPLOYMENTS"
DATA_SCOPE_ACTIVE_ONLY = "ALL_ACTIVE_EMPLOYMENTS"
DATA_SCOPE_SELECTED_EMPLOYEES = "SELECTED_EMPLOYEES"
DATA_SCOPE_SELECTED_EMPLOYMENTS = "SELECTED_EMPLOYMENTS"

IP_RESTRICTION_NONE = "NONE"
IP_RESTRICTION_SERVER_MANAGED = "SERVER_MANAGED"

EXPIRATION_NONE = "NONE"
EXPIRATION_30_DAYS = "DAYS_30"
EXPIRATION_90_DAYS = "DAYS_90"
EXPIRATION_1_YEAR = "YEAR_1"
EXPIRATION_CUSTOM = "CUSTOM_DATE"

PERMISSION_PROFILE_HEALTH = "HEALTH_ONLY"
PERMISSION_PROFILE_SHIFT_PLAN = "SHIFT_PLAN"
PERMISSION_PROFILE_ATTENDANCE = "ATTENDANCE_NO_PUNCHES"
PERMISSION_PROFILE_ATTENDANCE_WITH_PUNCHES = "ATTENDANCE_WITH_PUNCHES"
PERMISSION_PROFILE_FULL_READONLY = "FULL_READONLY"

_NAME_TOKEN_PATTERN = re.compile(r"^[0-9A-Za-z\u00C0-\u024F _-]+$")


@dataclass(frozen=True)
class ScopeDefinition:
    id: str
    label: str
    description: str
    data_access: str
    when_to_enable: str
    risk: str
    available: bool = True
    unavailable_reason: str | None = None


SCOPE_DEFINITIONS: tuple[ScopeDefinition, ...] = (
    ScopeDefinition(
        id=SCOPE_HEALTH,
        label="Kontrola dostupnosti API",
        description="Umožní ověřit, že integrační token funguje a rozhraní je dostupné.",
        data_access="Vrací jen technický health check bez provozních dat.",
        when_to_enable="Zapněte vždy, když externí partner potřebuje sledovat dostupnost integrace.",
        risk="Nízké riziko. Partner nezíská osobní údaje ani docházková data.",
    ),
    ScopeDefinition(
        id=SCOPE_EMPLOYMENTS,
        label="Čtení zaměstnanců a úvazků",
        description="Zpřístupní seznam zaměstnanců a jejich úvazků v povoleném rozsahu.",
        data_access="Vrací identifikátory zaměstnanců, employment_id, názvy úvazků a stav aktivity.",
        when_to_enable="Zapněte při prvotním párování lidí a úvazků v cílovém systému.",
        risk="Střední riziko. Partner získá seznam osob a pracovních vztahů.",
    ),
    ScopeDefinition(
        id=SCOPE_SHIFT_PLAN,
        label="Čtení plánu služeb",
        description="Umožní číst rozpis směn a volitelné měsíční zámky v povoleném rozsahu.",
        data_access="Vrací plánované příchody a odchody podle employment_id.",
        when_to_enable="Zapněte pro plánovací nebo mzdové systémy, které potřebují směny.",
        risk="Střední riziko. Partner získá budoucí pracovní rozvrh lidí.",
    ),
    ScopeDefinition(
        id=SCOPE_ATTENDANCE,
        label="Čtení skutečné docházky",
        description="Umožní číst skutečně evidovanou docházku včetně volitelných doplňkových dat.",
        data_access="Vrací skutečné příchody a odchody podle employment_id.",
        when_to_enable="Zapněte pro mzdy, reporting nebo přenos skutečně odpracované doby.",
        risk="Vyšší riziko. Partner získá citlivá provozní data o docházce.",
    ),
    ScopeDefinition(
        id=SCOPE_ATTENDANCE_CREATE,
        label="Vytváření docházky",
        description="Umožní založit nový docházkový záznam pro existující employment_id a datum.",
        data_access="Smí vytvořit pouze docházku. Nevytváří zaměstnance, uživatele ani úvazky.",
        when_to_enable="Zapněte jen pokud externí systém skutečně zapisuje docházku a má jasný proces pro duplicitní pokusy.",
        risk="Vysoké riziko. Partner může založit nový docházkový záznam v povoleném rozsahu.",
    ),
    ScopeDefinition(
        id=SCOPE_ATTENDANCE_UPDATE,
        label="Úprava docházky",
        description="Umožní měnit časy existujícího docházkového záznamu v povoleném rozsahu.",
        data_access="Smí upravovat pouze uložené časy docházky. Nemění zaměstnance, úvazky, plány služeb ani zámky.",
        when_to_enable="Zapněte jen pokud partner potřebuje opravovat nebo doplňovat docházku a umí pracovat s konflikty změn.",
        risk="Vysoké riziko. Partner může změnit existující docházkový záznam.",
    ),
    ScopeDefinition(
        id=SCOPE_ATTENDANCE_DELETE,
        label="Mazání docházky",
        description="Umožní odstranit existující docházkový záznam v povoleném rozsahu.",
        data_access="Smí mazat pouze docházku. Nemá oprávnění spravovat zaměstnance, úvazky, plány služeb ani zámky.",
        when_to_enable="Zapněte jen pokud je mazání nezbytné a partner má řízený auditovaný proces oprav.",
        risk="Kritické riziko. Partner může smazat docházkový záznam; používejte pouze po výslovném schválení správce.",
    ),
    ScopeDefinition(
        id=SCOPE_PUNCHES,
        label="Čtení odvozených průchodů",
        description="Umožní číst odvozené události příchod/odchod vytvořené z docházky.",
        data_access="Vrací odvozené průchody ARRIVAL a DEPARTURE podle attendance dat.",
        when_to_enable="Zapněte jen tehdy, když cílový systém očekává události po jednotlivých průchodech.",
        risk="Vyšší riziko. Partner získá detailnější provozní průběh dne.",
    ),
    ScopeDefinition(
        id=SCOPE_LOCKS,
        label="Čtení měsíčních zámků",
        description="Umožní číst, které měsíce jsou uzamčené proti změnám.",
        data_access="Vrací stav uzamčení měsíců podle employment_id.",
        when_to_enable="Zapněte, když partner potřebuje rozlišit uzavřené a otevřené období.",
        risk="Nízké až střední riziko. Jde o provozní metadata nad docházkou.",
    ),
    ScopeDefinition(
        id=SCOPE_OPENAPI,
        label="Čtení chráněné OpenAPI dokumentace",
        description="Umožní stáhnout strojově čitelné OpenAPI schéma integračního API.",
        data_access="Vrací jen dokumentaci endpointů, ne provozní data zaměstnanců.",
        when_to_enable="Zapněte, pokud partner napojuje klienta automatizovaně z OpenAPI.",
        risk="Nízké riziko. Partner získá jen technický popis API.",
    ),
    ScopeDefinition(
        id=SCOPE_CHANGES,
        label="Změnová synchronizace",
        description="Volba je připravená pro budoucí change feed, ale v této verzi ještě není dostupná.",
        data_access="V této verzi nevrací žádná data, protože endpoint není implementovaný.",
        when_to_enable="Nezapínejte. Pokud partner změnovou synchronizaci potřebuje, je nutné ji doplnit zvlášť.",
        risk="Bez provozního dopadu, protože endpoint není dostupný.",
        available=False,
        unavailable_reason="Není v této verzi podporováno.",
    ),
)

SCOPE_BY_ID = {item.id: item for item in SCOPE_DEFINITIONS}

PERMISSION_PROFILES: dict[str, tuple[str, ...]] = {
    PERMISSION_PROFILE_HEALTH: (SCOPE_HEALTH,),
    PERMISSION_PROFILE_SHIFT_PLAN: (SCOPE_HEALTH, SCOPE_EMPLOYMENTS, SCOPE_SHIFT_PLAN, SCOPE_LOCKS),
    PERMISSION_PROFILE_ATTENDANCE: (SCOPE_HEALTH, SCOPE_EMPLOYMENTS, SCOPE_ATTENDANCE, SCOPE_LOCKS),
    PERMISSION_PROFILE_ATTENDANCE_WITH_PUNCHES: (
        SCOPE_HEALTH,
        SCOPE_EMPLOYMENTS,
        SCOPE_ATTENDANCE,
        SCOPE_PUNCHES,
        SCOPE_LOCKS,
    ),
    PERMISSION_PROFILE_FULL_READONLY: (
        SCOPE_HEALTH,
        SCOPE_EMPLOYMENTS,
        SCOPE_SHIFT_PLAN,
        SCOPE_ATTENDANCE,
        SCOPE_PUNCHES,
        SCOPE_LOCKS,
        SCOPE_OPENAPI,
    ),
}

PERMISSION_PROFILE_LABELS: dict[str, str] = {
    PERMISSION_PROFILE_HEALTH: "Pouze kontrola dostupnosti",
    PERMISSION_PROFILE_SHIFT_PLAN: "Plán služeb",
    PERMISSION_PROFILE_ATTENDANCE: "Docházka bez průchodů",
    PERMISSION_PROFILE_ATTENDANCE_WITH_PUNCHES: "Docházka včetně odvozených průchodů",
    PERMISSION_PROFILE_FULL_READONLY: "Kompletní read-only integrace",
}

DATA_SCOPE_LABELS: dict[str, str] = {
    DATA_SCOPE_ALL: "Všechny úvazky",
    DATA_SCOPE_ACTIVE_ONLY: "Všechny aktivní úvazky",
    DATA_SCOPE_SELECTED_EMPLOYEES: "Pouze vybraní zaměstnanci",
    DATA_SCOPE_SELECTED_EMPLOYMENTS: "Pouze vybrané úvazky",
}

IP_RESTRICTION_LABELS: dict[str, str] = {
    IP_RESTRICTION_NONE: "Bez IP omezení",
    IP_RESTRICTION_SERVER_MANAGED: "IP omezení spravuje administrátor serveru mimo toto UI",
}

CLIENT_STATUS_LABELS: dict[str, str] = {
    models.IntegrationClientStatus.ACTIVE.value: "Aktivní",
    models.IntegrationClientStatus.DISABLED.value: "Deaktivovaná",
    models.IntegrationClientStatus.REVOKED.value: "Revokovaná",
    "EXPIRED": "Expirovaná",
}


def _normalize_string_list(values: list[str]) -> list[str]:
    return sorted({item.strip() for item in values if item and item.strip()})


def validate_client_name(raw_name: str) -> str:
    name = raw_name.strip()
    if len(name) < NAME_MIN_LENGTH:
        raise HTTPException(status_code=400, detail="Název integrace musí mít alespoň 3 znaky.")
    if len(name) > NAME_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="Název integrace může mít nejvýše 80 znaků.")
    if any(ord(ch) < 32 for ch in name):
        raise HTTPException(status_code=400, detail="Název integrace nesmí obsahovat řídicí znaky.")
    if not _NAME_TOKEN_PATTERN.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail="Název integrace může obsahovat jen písmena, číslice, mezery, pomlčky a podtržítka.",
        )
    lowered = name.lower()
    if "http://" in lowered or "https://" in lowered or "www." in lowered:
        raise HTTPException(status_code=400, detail="Název integrace nesmí být URL adresa.")
    if "<" in name or ">" in name:
        raise HTTPException(status_code=400, detail="Název integrace nesmí obsahovat HTML značky.")
    if lowered.startswith(("dgi_", "dg_", "sk-", "token", "secret")):
        raise HTTPException(status_code=400, detail="Název integrace nesmí vypadat jako token nebo tajný údaj.")
    return name


def validate_scopes(raw_scopes: list[str]) -> list[str]:
    scopes = _normalize_string_list(raw_scopes)
    for scope in scopes:
        definition = SCOPE_BY_ID.get(scope)
        if definition is None:
            raise HTTPException(status_code=400, detail=f"Neznámé oprávnění: {scope}.")
        if not definition.available:
            raise HTTPException(status_code=400, detail=f"Oprávnění {scope} není v této verzi podporováno.")
    if not scopes:
        raise HTTPException(status_code=400, detail="Vyberte alespoň jedno oprávnění integračního klienta.")
    if SCOPE_HEALTH not in scopes:
        raise HTTPException(status_code=400, detail="Každá integrace musí mít oprávnění pro kontrolu dostupnosti API.")
    return scopes


def expiration_from_choice(choice: str, custom_date: date | None) -> datetime | None:
    now = datetime.now(UTC)
    if choice == EXPIRATION_NONE:
        return None
    if choice == EXPIRATION_30_DAYS:
        return now + timedelta(days=30)
    if choice == EXPIRATION_90_DAYS:
        return now + timedelta(days=90)
    if choice == EXPIRATION_1_YEAR:
        return now + timedelta(days=365)
    if choice == EXPIRATION_CUSTOM:
        if custom_date is None:
            raise HTTPException(status_code=400, detail="Vyberte datum expirace.")
        if custom_date <= now.date():
            raise HTTPException(status_code=400, detail="Datum expirace musí být v budoucnosti.")
        return datetime.combine(custom_date, datetime.max.time(), tzinfo=UTC)
    raise HTTPException(status_code=400, detail="Neplatná volba expirace.")


def expiration_choice_from_datetime(expires_at: datetime | None) -> tuple[str, date | None]:
    if expires_at is None:
        return (EXPIRATION_NONE, None)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    else:
        expires_at = expires_at.astimezone(UTC)
    now = datetime.now(UTC)
    delta_days = (expires_at.date() - now.date()).days
    if 28 <= delta_days <= 31:
        return (EXPIRATION_30_DAYS, expires_at.date())
    if 88 <= delta_days <= 92:
        return (EXPIRATION_90_DAYS, expires_at.date())
    if 360 <= delta_days <= 370:
        return (EXPIRATION_1_YEAR, expires_at.date())
    return (EXPIRATION_CUSTOM, expires_at.date())


def build_employee_options(db: Session) -> list[dict[str, object]]:
    users = (
        db.execute(
            select(models.PortalUser)
            .options(selectinload(models.PortalUser.employments))
            .order_by(models.PortalUser.name.asc(), models.PortalUser.id.asc())
        )
        .scalars()
        .all()
    )
    options: list[dict[str, object]] = []
    for user in users:
        employments = sorted(user.employments, key=lambda item: (item.start_date, item.id))
        active_count = sum(1 for item in employments if item.is_active)
        label = (user.name or "").strip() or (user.email or "").strip() or f"Uživatel {user.id}"
        options.append(
            {
                "id": user.id,
                "label": label,
                "email": (user.email or "").strip(),
                "is_active": bool(user.is_active),
                "employment_count": len(employments),
                "active_employment_count": active_count,
                "employment_labels": [employment_label(item, user_name=user.name) for item in employments],
            }
        )
    return options


def build_employment_options(db: Session) -> list[dict[str, object]]:
    employments = (
        db.execute(
            select(models.Employment)
            .options(selectinload(models.Employment.user))
            .order_by(models.Employment.start_date.asc(), models.Employment.id.asc())
        )
        .scalars()
        .all()
    )
    options: list[dict[str, object]] = []
    for employment in employments:
        options.append(
            {
                "id": employment.id,
                "user_id": employment.user_id,
                "label": employment_label(employment, user_name=getattr(employment.user, "name", None)),
                "employment_type": employment.employment_type,
                "start_date": employment.start_date.isoformat(),
                "end_date": employment.end_date.isoformat() if employment.end_date is not None else None,
                "is_active": employment.is_active,
            }
        )
    return options


def validate_selected_employee_ids(db: Session, employee_ids: list[int]) -> list[int]:
    if not employee_ids:
        raise HTTPException(status_code=400, detail="Vyberte alespoň jednoho zaměstnance.")
    normalized = sorted({int(item) for item in employee_ids})
    existing = {
        int(item)
        for item in db.execute(select(models.PortalUser.id).where(models.PortalUser.id.in_(normalized))).scalars().all()
    }
    missing = [item for item in normalized if item not in existing]
    if missing:
        raise HTTPException(status_code=400, detail=f"Neexistující zaměstnanec: {missing[0]}.")
    return normalized


def validate_selected_employment_ids(db: Session, employment_ids: list[int]) -> list[int]:
    if not employment_ids:
        raise HTTPException(status_code=400, detail="Vyberte alespoň jeden úvazek.")
    normalized = sorted({int(item) for item in employment_ids})
    existing = {
        int(item)
        for item in db.execute(select(models.Employment.id).where(models.Employment.id.in_(normalized))).scalars().all()
    }
    missing = [item for item in normalized if item not in existing]
    if missing:
        raise HTTPException(status_code=400, detail=f"Neexistující employment_id: {missing[0]}.")
    return normalized


def summarize_scopes(scopes: list[str]) -> str:
    if not scopes:
        return "Žádná oprávnění"
    labels = [SCOPE_BY_ID[item].label for item in scopes if item in SCOPE_BY_ID]
    return ", ".join(labels) if labels else ", ".join(scopes)


def _client_is_expired(client: models.IntegrationClient) -> bool:
    expires_at = client.expires_at
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    else:
        expires_at = expires_at.astimezone(UTC)
    return expires_at <= datetime.now(UTC)


def client_status_key(client: models.IntegrationClient) -> str:
    if _client_is_expired(client):
        return "EXPIRED"
    return str(client.status)


def client_status_label(client: models.IntegrationClient) -> str:
    return CLIENT_STATUS_LABELS.get(client_status_key(client), "Neznámý stav")


def infer_ip_restriction_mode(client: models.IntegrationClient) -> str:
    return IP_RESTRICTION_SERVER_MANAGED if list(client.ip_allowlist or []) else IP_RESTRICTION_NONE


def infer_data_scope_mode(client: models.IntegrationClient) -> str:
    raw_mode = str(getattr(client, "data_scope_mode", "") or "").strip()
    if raw_mode in {
        DATA_SCOPE_ALL,
        DATA_SCOPE_ACTIVE_ONLY,
        DATA_SCOPE_SELECTED_EMPLOYEES,
        DATA_SCOPE_SELECTED_EMPLOYMENTS,
    }:
        return raw_mode
    if client.allowed_employee_ids:
        return DATA_SCOPE_SELECTED_EMPLOYEES
    if client.allowed_employment_ids:
        return DATA_SCOPE_SELECTED_EMPLOYMENTS
    return DATA_SCOPE_ALL


def data_scope_summary(client: models.IntegrationClient) -> str:
    mode = infer_data_scope_mode(client)
    if mode == DATA_SCOPE_SELECTED_EMPLOYEES:
        count = len(client.allowed_employee_ids or [])
        suffix = "včetně neaktivních úvazků" if bool(getattr(client, "include_inactive_employments", False)) else "jen aktivní úvazky"
        return f"Vybraní zaměstnanci ({count}) - {suffix}"
    if mode == DATA_SCOPE_SELECTED_EMPLOYMENTS:
        count = len(client.allowed_employment_ids or [])
        return f"Vybrané úvazky ({count})"
    if mode == DATA_SCOPE_ACTIVE_ONLY:
        return "Všechny aktivní úvazky"
    return "Všechny úvazky"


def allowed_actions_for_client(client: models.IntegrationClient) -> list[str]:
    status_key = client_status_key(client)
    actions = ["rotate"]
    if status_key in {models.IntegrationClientStatus.ACTIVE.value, "EXPIRED"}:
        actions.append("disable")
    if status_key == models.IntegrationClientStatus.DISABLED.value:
        actions.append("enable")
    if status_key != models.IntegrationClientStatus.REVOKED.value:
        actions.append("revoke")
    return actions


def build_recent_audit_summary(db: Session, client_id: int) -> dict[str, object]:
    rows = (
        db.execute(
            select(models.IntegrationAuditLog)
            .where(models.IntegrationAuditLog.client_id == client_id)
            .order_by(models.IntegrationAuditLog.requested_at.desc(), models.IntegrationAuditLog.id.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    if not rows:
        return {
            "request_count": 0,
            "last_error": None,
            "last_source_ip": None,
            "last_path": None,
        }
    last_error = next((row for row in rows if row.error_code or row.status_code >= 400), None)
    last_source_ip = rows[0].source_ip
    masked_ip = None
    if last_source_ip:
        masked_ip = f"{last_source_ip[:6]}..." if len(last_source_ip) > 6 else last_source_ip
    return {
        "request_count": len(rows),
        "last_error": (
            {
                "status_code": last_error.status_code,
                "error_code": last_error.error_code,
                "requested_at": last_error.requested_at.astimezone(UTC).isoformat(),
            }
            if last_error is not None
            else None
        ),
        "last_source_ip": masked_ip,
        "last_path": rows[0].path,
    }


def count_active_clients(db: Session) -> int:
    return int(
        db.execute(
            select(func.count(models.IntegrationClient.id)).where(
                models.IntegrationClient.status == models.IntegrationClientStatus.ACTIVE.value
            )
        ).scalar_one()
    )
