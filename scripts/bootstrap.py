"""Install SQL pack v0.3 into an empty database, then apply VGG OS migrations.

Re-running this script does not re-run install.sql once core.schema_version exists.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "vendor" / "VGG_OS_SQL_Pack_v0_3.zip"
MIGRATIONS = ROOT / "db" / "migrations"
SECRETS = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "vggos" / "secrets.env"
CONTAINER = "vggos-postgres"


def main() -> None:
    values = read_env(SECRETS)
    if "POSTGRES_PASSWORD" not in values:
        values["POSTGRES_PASSWORD"] = secrets.token_hex(18)
    if "VGGOS_APP_PASSWORD" not in values:
        values["VGGOS_APP_PASSWORD"] = secrets.token_hex(18)
    if "VGGOS_SESSION_SECRET" not in values:
        values["VGGOS_SESSION_SECRET"] = secrets.token_hex(32)
    if "VGGOS_TOKEN_KEY" not in values:
        values["VGGOS_TOKEN_KEY"] = secrets.token_hex(32)
    values.setdefault("VGG_AUTH_URL", "https://qnorggoycwbbxdlvbcvq.supabase.co")
    values.setdefault("VGG_AUTH_ANON_KEY", "")
    values.setdefault("VGGOS_DEV_USERNAME", "developer")
    values.setdefault("VGGOS_BC_BASE_URL", "")
    values.setdefault("VGGOS_BC_API_ROUTE", "")
    values.setdefault("VGGOS_BC_AUTH_METHOD", "")
    values.setdefault("VGGOS_BC_USERNAME", "")
    values.setdefault("VGGOS_BC_ACCESS_KEY", "")
    values.setdefault("VGGOS_BC_SECRET_REFERENCE", "file://local-vggos-secrets#VGGOS_BC_ACCESS_KEY")
    app_password = values["VGGOS_APP_PASSWORD"]
    admin_password = values["POSTGRES_PASSWORD"]
    values["VGGOS_DATABASE_URL"] = pg_url("vggos_app", app_password, "vggos")
    values["VGGOS_MIGRATION_DATABASE_URL"] = pg_url("vggos", admin_password, "vggos")
    values["VGGOS_TEST_DATABASE_URL"] = pg_url("vggos_app", app_password, "vggos_test")
    write_env(SECRETS, values)

    env = os.environ.copy()
    env["POSTGRES_PASSWORD"] = admin_password
    subprocess.run(["docker", "compose", "up", "-d"], cwd=ROOT, env=env, check=True)
    wait_for_postgres(values["VGGOS_MIGRATION_DATABASE_URL"])

    with tempfile.TemporaryDirectory() as tmp:
        pack_dir = Path(tmp) / "pack"
        with zipfile.ZipFile(PACK) as archive:
            archive.extractall(pack_dir)
        extracted = pack_dir / "VGG_OS_SQL_Pack_v0_3"
        verify_pack(extracted)
        subprocess.run(["docker", "exec", CONTAINER, "rm", "-rf", "/tmp/vggos-pack", "/tmp/vggos-migrate"], check=False)
        subprocess.run(["docker", "cp", str(extracted), f"{CONTAINER}:/tmp/vggos-pack"], check=True)
        subprocess.run(["docker", "cp", str(MIGRATIONS), f"{CONTAINER}:/tmp/vggos-migrate"], check=True)
        install_database("vggos")
        ensure_database("vggos_test", values["VGGOS_MIGRATION_DATABASE_URL"])
        install_database("vggos_test")

    ensure_role(values["VGGOS_MIGRATION_DATABASE_URL"], app_password)
    apply_migrations("vggos")
    apply_migrations("vggos_test")
    print(f"Secrets file: {SECRETS}")
    from seed_temp_logins import main as seed_temp_logins

    seed_temp_logins()
    print("Sign-in is the appraisal project. Temporary database accounts are also installed.")
    print("Set VGGOS_TEMP_DB_LOGIN=0 to ignore those accounts. VGG_AUTH_ANON_KEY stays in the secrets file.")
    print("Browser origins: http://127.0.0.1:8096, http://localhost:8096, https://vggos.vgg.tools")
    print("Business Central URL and access key are blank. No call has been made.")


def install_database(name: str) -> None:
    admin = admin_url(name)
    if schema_version_present(admin):
        print(f"{name}: SQL pack already installed. Installer was not run again.")
        return
    if schema_present(admin):
        raise SystemExit(
            f"{name} has a partial schema and no core.schema_version. "
            "Do not re-run install.sql. Drop that database and run this script again."
        )
    psql(name, "/tmp/vggos-pack/install.sql")
    output = psql(name, "/tmp/vggos-pack/tests/001_regression.sql")
    if "FAILED:" in output:
        raise SystemExit(output)
    psql(name, "/tmp/vggos-pack/tests/002_catalogue_checks.sql")
    print(f"{name}: SQL pack v0.3 installed and pack tests passed.")


def apply_migrations(name: str) -> None:
    admin = admin_url(name)
    applied = applied_migrations(admin)
    if "001_advancly_slice" not in applied:
        psql(name, "/tmp/vggos-migrate/001_advancly_slice.sql")
    if "002_app_grants" not in applied:
        psql(name, "/tmp/vggos-migrate/002_app_grants.sql")
    if "003_appraisal_iam" not in applied:
        psql(name, "/tmp/vggos-migrate/003_appraisal_iam.sql")
    if "004_temp_db_login" not in applied:
        psql(name, "/tmp/vggos-migrate/004_temp_db_login.sql")
    if "005_repository" not in applied:
        psql(name, "/tmp/vggos-migrate/005_repository.sql")
    if "006_bank_ledger_and_write_scopes" not in applied:
        psql(name, "/tmp/vggos-migrate/006_bank_ledger_and_write_scopes.sql")
    output = psql(name, "/tmp/vggos-pack/tests/001_regression.sql")
    if "FAILED:" in output:
        raise SystemExit(output)
    psql(name, "/tmp/vggos-pack/tests/002_catalogue_checks.sql")
    print(f"{name}: migrations applied and pack tests passed.")


def ensure_role(admin_url_value: str, password: str) -> None:
    with psycopg.connect(admin_url_value, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'vggos_app'").fetchone()
        statement = sql.SQL("ALTER ROLE vggos_app PASSWORD {}" if exists else "CREATE ROLE vggos_app LOGIN PASSWORD {}")
        conn.execute(statement.format(sql.Literal(password)))
        conn.execute("GRANT CONNECT ON DATABASE vggos TO vggos_app")
        conn.execute("GRANT CONNECT ON DATABASE vggos_test TO vggos_app")


def ensure_database(name: str, admin_url_value: str) -> None:
    with psycopg.connect(admin_url_value, autocommit=True) as conn:
        found = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not found:
            conn.execute(f'CREATE DATABASE "{name}" OWNER vggos')


def schema_present(url: str) -> bool:
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'core'"
        ).fetchone()
    return row is not None


def schema_version_present(url: str) -> bool:
    if not schema_present(url):
        return False
    with psycopg.connect(url) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'core' AND table_name = 'schema_version'
            """
        ).fetchone()
        if row is None:
            return False
        version = conn.execute("SELECT version FROM core.schema_version WHERE version = '0.3.0'").fetchone()
    return version is not None


def applied_migrations(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        exists = conn.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'core' AND table_name = 'app_migration'
            """
        ).fetchone()
        if exists is None:
            return set()
        rows = conn.execute("SELECT version FROM core.app_migration").fetchall()
    return {row[0] for row in rows}


def admin_url(database: str) -> str:
    values = read_env(SECRETS)
    return pg_url("vggos", values["POSTGRES_PASSWORD"], database)


def wait_for_postgres(url: str) -> None:
    for _ in range(60):
        try:
            with psycopg.connect(url, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except Exception:
            time.sleep(1)
    raise SystemExit("PostgreSQL did not accept connections on 127.0.0.1:54329.")


def psql(database: str, remote_path: str) -> str:
    result = subprocess.run(
        ["docker", "exec", CONTAINER, "psql", "-U", "vggos", "-d", database, "-v", "ON_ERROR_STOP=1", "-f", remote_path],
        check=False,
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise SystemExit(output)
    return output


def verify_pack(root: Path) -> None:
    for line in (root / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([a-f0-9]{64})\s+(.+)$", line.strip())
        if not match:
            continue
        digest, name = match.groups()
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if actual != digest:
            raise SystemExit(f"SQL pack checksum mismatch: {name}")


def pg_url(user: str, password: str, database: str) -> str:
    return f"postgresql://{user}:{password}@127.0.0.1:54329/{database}"


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
