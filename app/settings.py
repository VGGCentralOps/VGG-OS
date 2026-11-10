"""Server configuration. Business Central secrets stay in a file outside the repository."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.bc_url import ConfigError, normalise_api_route, resolve_api_root
from app.constants import SECRET_REFERENCE
from app.iam_config import AUTH_BASE, require_anon_key

DEFAULT_SECRETS = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "vggos" / "secrets.env"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


@dataclass
class Settings:
    database_url: str
    migration_database_url: str
    test_database_url: str
    dev_username: str
    dev_password: str
    session_secret: str
    bc_base_url: str
    bc_api_route: str
    bc_auth_method: str
    bc_username: str
    bc_access_key: str
    bc_secret_reference: str
    secrets_file: str
    auth_url: str
    auth_anon_key: str
    token_key: str
    temp_db_login: bool
    bc_allow_private_http: bool = False
    bc_entity_map: str = ""
    trusted_proxy_hops: int = 0
    bc_company_id: str = ""

    def company_key(self) -> str:
        """The Advancly id to match in the companies list. Never chosen by name."""
        from app.constants import ADVANCLY_COMPANY_ID

        value = (self.bc_company_id or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value):
            return value
        return ADVANCLY_COMPANY_ID

    def secret_values(self) -> list[str]:
        return [
            self.bc_access_key,
            self.bc_username,
            self.dev_password,
            self.session_secret,
            self.auth_anon_key,
            self.token_key,
        ]

    def bc_state(self) -> str:
        try:
            self.require_bc()
        except ConfigError:
            return "not_configured"
        return "configured"

    def connection_blockers(self) -> list[str]:
        blockers = []
        if not self.bc_base_url:
            blockers.append("Business Central base URL (VGGOS_BC_BASE_URL) in the server secrets file.")
            try:
                normalise_api_route(self.bc_api_route)
            except ConfigError as exc:
                blockers.append(str(exc))
        else:
            try:
                resolve_api_root(self.bc_base_url, self.bc_api_route, self.bc_allow_private_http)
            except ConfigError as exc:
                blockers.append(str(exc))
        if not self.bc_auth_method:
            blockers.append("Authentication method. The two Business Central instances do not authenticate the same way.")
        elif self.bc_auth_method.lower() in {"negotiate", "kerberos", "windows"}:
            blockers.append("This host cannot send Windows/Kerberos. Ask Elijah before any call is made.")
        elif self.bc_auth_method.lower() not in {"basic", "basic_access_key"}:
            blockers.append("Authentication method is not basic access-key. Confirm it with Elijah.")
        if not self.bc_username:
            blockers.append("Business Central user name, in the server secret file.")
        if not self.bc_access_key:
            blockers.append("Web service access key, in the server secret file. It is not entered in the browser.")
        if self.bc_access_key and self.bc_access_key in (self.bc_base_url or ""):
            blockers.append("The base URL contains the access key. Remove it from the URL.")
        return blockers

    def require_bc(self) -> str:
        blockers = self.connection_blockers()
        if blockers:
            raise ConfigError("Business Central is not being called. " + " ".join(blockers))
        return resolve_api_root(self.bc_base_url, self.bc_api_route, self.bc_allow_private_http)


def _enabled(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _hops(value: str) -> int:
    try:
        return max(0, min(3, int(value.strip() or "0")))
    except ValueError:
        return 0


def load_settings() -> Settings:
    path = Path(os.environ.get("VGGOS_SECRETS_FILE", str(DEFAULT_SECRETS)))
    file_values = read_env_file(path)
    def pick(name: str, default: str = "") -> str:
        if name in os.environ and os.environ[name] != "":
            return os.environ[name]
        return file_values.get(name, default)

    settings = Settings(
        database_url=pick("VGGOS_DATABASE_URL"),
        migration_database_url=pick("VGGOS_MIGRATION_DATABASE_URL"),
        test_database_url=pick("VGGOS_TEST_DATABASE_URL"),
        dev_username=pick("VGGOS_DEV_USERNAME", "developer"),
        dev_password=pick("VGGOS_DEV_PASSWORD"),
        session_secret=pick("VGGOS_SESSION_SECRET"),
        bc_base_url=pick("VGGOS_BC_BASE_URL"),
        bc_api_route=pick("VGGOS_BC_API_ROUTE"),
        bc_auth_method=pick("VGGOS_BC_AUTH_METHOD"),
        bc_username=pick("VGGOS_BC_USERNAME"),
        bc_access_key=pick("VGGOS_BC_ACCESS_KEY"),
        bc_secret_reference=pick("VGGOS_BC_SECRET_REFERENCE", SECRET_REFERENCE),
        secrets_file=str(path),
        auth_url=pick("VGG_AUTH_URL", AUTH_BASE),
        auth_anon_key=pick("VGG_AUTH_ANON_KEY"),
        token_key=pick("VGGOS_TOKEN_KEY"),
        temp_db_login=_enabled(pick("VGGOS_TEMP_DB_LOGIN", "1")),
        bc_allow_private_http=pick("VGGOS_BC_ALLOW_PRIVATE_HTTP", "0").strip() == "1",
        bc_entity_map=pick("VGGOS_BC_ENTITY_MAP"),
        trusted_proxy_hops=_hops(pick("VGGOS_TRUSTED_PROXY_HOPS", "0")),
        bc_company_id=pick("VGGOS_BC_COMPANY_ID"),
    )
    if len(settings.session_secret) < 16:
        raise RuntimeError(f"Session secret is missing. Expected it in {path}.")
    require_anon_key(settings.auth_anon_key)
    return settings
