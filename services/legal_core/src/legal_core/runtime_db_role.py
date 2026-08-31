"""Provision the least-privilege PostgreSQL login used by Legal Core at runtime.

The bootstrap/owner login remains responsible for migrations. Runtime code authenticates with a
separate role that cannot bypass RLS or create database objects. The command is intentionally
idempotent and may run both before and after migrations so default and existing-object grants stay
consistent on upgraded deployments.
"""

from __future__ import annotations

import os
import re

import psycopg
from psycopg import sql

_RUNTIME_ROLE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


def provision_runtime_role() -> str:
    owner_user = _required_environment("POSTGRES_USER")
    owner_password = _required_environment("POSTGRES_PASSWORD")
    database = os.getenv("POSTGRES_DB", "dental_legal").strip() or "dental_legal"
    host = os.getenv("POSTGRES_HOST", "localhost").strip() or "localhost"
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    app_user = os.getenv("POSTGRES_APP_USER", "dental_legal_app").strip()
    app_password = _required_environment("POSTGRES_APP_PASSWORD")

    if _RUNTIME_ROLE.fullmatch(app_user) is None:
        raise RuntimeError("POSTGRES_APP_USER must be a simple lowercase PostgreSQL role name")
    if app_user == owner_user:
        raise RuntimeError("runtime PostgreSQL role must differ from the database owner")
    if len(app_password) < 32:
        raise RuntimeError("POSTGRES_APP_PASSWORD must contain at least 32 characters")
    if not 1 <= port <= 65535:
        raise RuntimeError("POSTGRES_PORT is outside the valid range")

    with psycopg.connect(
        host=host,
        port=port,
        dbname=database,
        user=owner_user,
        password=owner_password,
        autocommit=True,
    ) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_user,))
        exists = cursor.fetchone() is not None
        role = sql.Identifier(app_user)
        password = sql.Literal(app_password)
        if not exists:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(role, password)
            )
        else:
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(role, password)
            )

        cursor.execute(sql.SQL("ALTER ROLE {} SET search_path = public").format(role))
        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database),
                role,
            )
        )
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(role))

        owner = sql.Identifier(owner_user)
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ).format(owner, role)
        )
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {}"
            ).format(owner, role)
        )
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "GRANT EXECUTE ON FUNCTIONS TO {}"
            ).format(owner, role)
        )

        # Re-running after migrations covers existing deployments and objects created before the
        # default-privilege policy was installed.
        cursor.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
            ).format(role)
        )
        cursor.execute(
            sql.SQL(
                "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {}"
            ).format(role)
        )
        cursor.execute(
            sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {}").format(role)
        )

    return app_user


def main() -> None:
    role = provision_runtime_role()
    print(f"runtime PostgreSQL role ready: {role}")


if __name__ == "__main__":
    main()
