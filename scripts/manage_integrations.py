from __future__ import annotations

import argparse
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import models
from app.db.session import session_scope
from app.security.integration_tokens import build_token_record, generate_integration_token


def _parse_csv_ints(value: str | None) -> list[int]:
    if not value:
        return []
    return sorted({int(item.strip()) for item in value.split(",") if item.strip()})


def _parse_csv_strings(value: str | None) -> list[str]:
    if not value:
        return []
    return sorted({item.strip() for item in value.split(",") if item.strip()})


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _get_client(db, client_id: int) -> models.IntegrationClient:
    client = db.execute(
        select(models.IntegrationClient)
        .options(selectinload(models.IntegrationClient.secrets))
        .where(models.IntegrationClient.id == client_id)
    ).scalar_one_or_none()
    if client is None:
        raise SystemExit(f"Integrační klient {client_id} nebyl nalezen.")
    return client


def cmd_list(args) -> None:
    with session_scope() as db:
        rows = db.execute(
            select(models.IntegrationClient).options(selectinload(models.IntegrationClient.secrets)).order_by(models.IntegrationClient.id.asc())
        ).scalars().all()
        for client in rows:
            active_secret = next((item for item in sorted(client.secrets, key=lambda row: row.id, reverse=True) if item.revoked_at is None), None)
            print(
                f"id={client.id} name={client.name} status={client.status} "
                f"fingerprint={active_secret.token_fingerprint if active_secret else '-'} "
                f"last4={active_secret.token_last4 if active_secret else '-'} "
                f"expires_at={client.expires_at.isoformat() if client.expires_at else '-'}"
            )


def cmd_create(args) -> None:
    settings = get_settings()
    with session_scope() as db:
        client = models.IntegrationClient(
            name=args.name.strip(),
            status=models.IntegrationClientStatus.ACTIVE.value,
            scopes=_parse_csv_strings(args.scopes),
            allowed_employment_ids=_parse_csv_ints(args.allowed_employment_ids),
            allowed_employee_ids=_parse_csv_ints(args.allowed_employee_ids),
            ip_allowlist=_parse_csv_strings(args.ip_allowlist),
            expires_at=_parse_optional_datetime(args.expires_at),
            created_by=args.created_by,
        )
        db.add(client)
        db.flush()
        plaintext = generate_integration_token(settings)
        token_record = build_token_record(plaintext)
        db.add(
            models.IntegrationClientSecret(
                client_id=client.id,
                token_hash=token_record.token_hash,
                token_prefix=token_record.token_prefix,
                token_last4=token_record.token_last4,
                token_fingerprint=token_record.token_fingerprint,
            )
        )
        print(f"client_id={client.id}")
        print(f"token={plaintext}")
        print(f"fingerprint={token_record.token_fingerprint}")
        print(f"last4={token_record.token_last4}")


def cmd_rotate(args) -> None:
    settings = get_settings()
    with session_scope() as db:
        client = _get_client(db, args.client_id)
        now = datetime.now(UTC)
        for secret in client.secrets:
            if secret.revoked_at is None:
                secret.revoked_at = now
                secret.rotated_at = now
                db.add(secret)
        plaintext = generate_integration_token(settings)
        token_record = build_token_record(plaintext)
        db.add(
            models.IntegrationClientSecret(
                client_id=client.id,
                token_hash=token_record.token_hash,
                token_prefix=token_record.token_prefix,
                token_last4=token_record.token_last4,
                token_fingerprint=token_record.token_fingerprint,
            )
        )
        print(f"client_id={client.id}")
        print(f"token={plaintext}")
        print(f"fingerprint={token_record.token_fingerprint}")
        print(f"last4={token_record.token_last4}")


def cmd_disable(args) -> None:
    with session_scope() as db:
        client = _get_client(db, args.client_id)
        client.status = models.IntegrationClientStatus.DISABLED.value
        db.add(client)
        print(f"client_id={client.id} status={client.status}")


def cmd_enable(args) -> None:
    with session_scope() as db:
        client = _get_client(db, args.client_id)
        client.status = models.IntegrationClientStatus.ACTIVE.value
        db.add(client)
        print(f"client_id={client.id} status={client.status}")


def cmd_revoke(args) -> None:
    with session_scope() as db:
        client = _get_client(db, args.client_id)
        now = datetime.now(UTC)
        for secret in client.secrets:
            if secret.revoked_at is None:
                secret.revoked_at = now
                db.add(secret)
        client.status = models.IntegrationClientStatus.REVOKED.value
        db.add(client)
        print(f"client_id={client.id} status={client.status}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Správa integračních klientů DAGMAR.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.set_defaults(func=cmd_list)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--name", required=True)
    create_parser.add_argument("--scopes", default="")
    create_parser.add_argument("--allowed-employment-ids", dest="allowed_employment_ids", default="")
    create_parser.add_argument("--allowed-employee-ids", dest="allowed_employee_ids", default="")
    create_parser.add_argument("--ip-allowlist", dest="ip_allowlist", default="")
    create_parser.add_argument("--expires-at", dest="expires_at", default=None)
    create_parser.add_argument("--created-by", dest="created_by", default=None)
    create_parser.set_defaults(func=cmd_create)

    rotate_parser = subparsers.add_parser("rotate")
    rotate_parser.add_argument("client_id", type=int)
    rotate_parser.set_defaults(func=cmd_rotate)

    disable_parser = subparsers.add_parser("disable")
    disable_parser.add_argument("client_id", type=int)
    disable_parser.set_defaults(func=cmd_disable)

    enable_parser = subparsers.add_parser("enable")
    enable_parser.add_argument("client_id", type=int)
    enable_parser.set_defaults(func=cmd_enable)

    revoke_parser = subparsers.add_parser("revoke")
    revoke_parser.add_argument("client_id", type=int)
    revoke_parser.set_defaults(func=cmd_revoke)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
