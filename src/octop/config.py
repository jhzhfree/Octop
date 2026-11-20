"""Process-level configuration (config.json + env overrides)."""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

logger = logging.getLogger(__name__)

DEFAULT_MAX_UPLOAD_MB = 100
MIN_MAX_UPLOAD_MB = 1
MAX_MAX_UPLOAD_MB = 1024
DEFAULT_BROWSER_IDLE_TIMEOUT_MINUTES = 30

_VALID_DRIVERS = frozenset({"sqlite", "postgresql"})

_DATABASE_ENV_KEYS = (
    "OCTOP_DATABASE_URL",
    "OCTOP_DATABASE_DRIVER",
    "OCTOP_DATABASE_SQLITE_PATH",
    "OCTOP_DATABASE_HOST",
    "OCTOP_DATABASE_PORT",
    "OCTOP_DATABASE_NAME",
    "OCTOP_DATABASE_USER",
    "OCTOP_DATABASE_PASSWORD",
)


def database_env_configured() -> bool:
    """True when any ``OCTOP_DATABASE_*`` env var is set."""
    return any(os.environ.get(k) for k in _DATABASE_ENV_KEYS)


@dataclass(frozen=True)
class DatabaseConfig:
    """Database connection settings (SQLite or PostgreSQL)."""

    driver: str = "sqlite"
    sqlite_path: str = "octop.db"
    host: str = "127.0.0.1"
    port: int = 5432
    database: str = "octop"
    user: str = "octop"
    password: str | None = None
    url: str | None = None  # verbatim OCTOP_DATABASE_URL when set

    @property
    def is_sqlite(self) -> bool:
        return self.driver == "sqlite"

    @property
    def is_postgresql(self) -> bool:
        return self.driver == "postgresql"

    def resolve_sqlite_path(self, octop_root: Path) -> Path:
        """Absolute path to the SQLite file (relative paths are under ``octop_root``)."""
        p = Path(self.sqlite_path)
        return p if p.is_absolute() else octop_root / p

    def postgresql_conninfo(self) -> str:
        """Return a libpq/psycopg conninfo URL for PostgreSQL."""
        if self.driver != "postgresql":
            raise ValueError("postgresql_conninfo() requires driver=postgresql")
        if self.url:
            return self.url
        user = quote_plus(self.user)
        auth = user if not self.password else f"{user}:{quote_plus(self.password)}"
        return f"postgresql://{auth}@{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class TlsConfig:
    """TLS / Let's Encrypt settings persisted in config.json."""

    enabled: bool = False
    mode: str = ""
    domains: list[str] = field(default_factory=list)
    cert_file: str = ""
    key_file: str = ""
    issued_at: str = ""
    expires_at: str = ""
    acme_staging: bool = False
    http_port: int = 80


@dataclass(frozen=True)
class BackupConfig:
    """Automatic system backup settings persisted in config.json."""

    auto_enabled: bool = False
    schedule: str = "cron:0 4 * * *"
    retention_count: int = 7
    include_config: bool = True
    include_workspaces: bool = True
    include_skill_packages: bool = True
    include_plugins: bool = True
    include_knowledge: bool = True
    include_chats: bool = False


_VALID_MOBILE_BACKENDS = frozenset({"physical", "redroid", "emulator", "none"})


@dataclass(frozen=True)
class MobileCapabilities:
    """Install-time host capability for Remote Android (``capabilities.mobile``)."""

    enabled: bool = False
    backend: str = "none"
    probed_at: str = ""
    reason: str = ""


@dataclass(frozen=True)
class CapabilitiesConfig:
    mobile: MobileCapabilities = field(default_factory=MobileCapabilities)


@dataclass(frozen=True)
class OctopConfig:
    bind_host: str = "127.0.0.1"
    port: int = 8088
    log_level: str = "info"
    access_token_ttl_seconds: int = 86400
    session_absolute_timeout_seconds: int = 0
    session_idle_timeout_seconds: int = 0
    login_max_attempts: int = 5
    login_lockout_seconds: int = 900
    cors_origins: list[str] = field(default_factory=list)
    default_timezone: str = "Asia/Shanghai"
    enable_dashboard: bool = True
    enable_api_docs: bool = False
    history_v2_enabled: bool = False
    require_setup_password: bool = True
    user_authentication_mode: str = "legacy"
    unified_oidc_issuer: str = ""
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    # False when config.json omits ``database`` (use PathLayout.db unless env overrides).
    database_in_file: bool = False
    tls: TlsConfig = field(default_factory=TlsConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    capabilities: CapabilitiesConfig = field(default_factory=CapabilitiesConfig)
    max_upload_mb: int = DEFAULT_MAX_UPLOAD_MB
    browser_idle_timeout_minutes: int = DEFAULT_BROWSER_IDLE_TIMEOUT_MINUTES

    def __post_init__(self) -> None:
        if self.user_authentication_mode not in {"legacy", "unified_oidc"}:
            raise ValueError("user_authentication_mode must be legacy or unified_oidc")
        if self.user_authentication_mode == "unified_oidc":
            issuer = urlparse(self.unified_oidc_issuer)
            loopback_http = issuer.scheme == "http" and issuer.hostname in {
                "localhost",
                "127.0.0.1",
                "::1",
            }
            if (
                not issuer.hostname
                or issuer.username is not None
                or issuer.query
                or issuer.fragment
                or (issuer.scheme != "https" and not loopback_http)
            ):
                raise ValueError("unified_oidc requires a trusted HTTPS unified_oidc_issuer")
        if self.session_idle_timeout_seconds != 0 and self.session_idle_timeout_seconds < 120:
            raise ValueError("session_idle_timeout_seconds must be 0 or at least 120 seconds")
        if self.session_absolute_timeout_seconds < 0:
            raise ValueError("session_absolute_timeout_seconds must be non-negative")
        # JWT iat/exp and activity cadence use whole seconds. A one-second
        # lifetime has no valid second in which renewal can extend expiry.
        if self.access_token_ttl_seconds < 2:
            raise ValueError("access_token_ttl_seconds must be at least 2 seconds")

    @property
    def max_upload_bytes(self) -> int:
        return upload_mb_to_bytes(self.max_upload_mb)


def _defaults_for_file() -> dict[str, Any]:
    data = asdict(OctopConfig())
    data.pop("database_in_file", None)
    db = dict(data["database"])
    db.pop("password", None)
    db.pop("url", None)
    data["database"] = db
    return data


def _parse_tls_section(raw: object) -> TlsConfig:
    if raw is None:
        return TlsConfig()
    if not isinstance(raw, dict):
        msg = f"config.tls must be an object, got {type(raw).__name__}"
        raise ValueError(msg)
    domains_raw = raw.get("domains") or []
    if not isinstance(domains_raw, list):
        msg = "config.tls.domains must be a list"
        raise ValueError(msg)
    return TlsConfig(
        enabled=bool(raw.get("enabled", False)),
        mode=str(raw.get("mode", "")),
        domains=[str(d).strip() for d in domains_raw if str(d).strip()],
        cert_file=str(raw.get("cert_file", "")),
        key_file=str(raw.get("key_file", "")),
        issued_at=str(raw.get("issued_at", "")),
        expires_at=str(raw.get("expires_at", "")),
        acme_staging=bool(raw.get("acme_staging", False)),
        http_port=int(raw.get("http_port", 80)),
    )


def _parse_mobile_capabilities(raw: object) -> MobileCapabilities:
    if raw is None:
        return MobileCapabilities()
    if not isinstance(raw, dict):
        raise ValueError("config.capabilities.mobile must be an object")
    backend = str(raw.get("backend", "none")).strip().lower() or "none"
    if backend not in _VALID_MOBILE_BACKENDS:
        allowed = ", ".join(sorted(_VALID_MOBILE_BACKENDS))
        msg = f"capabilities.mobile.backend must be one of {allowed}, got {backend!r}"
        raise ValueError(msg)
    return MobileCapabilities(
        enabled=bool(raw.get("enabled", False)),
        backend=backend,
        probed_at=str(raw.get("probed_at", "")),
        reason=str(raw.get("reason", "")),
    )


def _parse_capabilities_section(raw: object) -> CapabilitiesConfig:
    if raw is None:
        return CapabilitiesConfig()
    if not isinstance(raw, dict):
        raise ValueError("config.capabilities must be an object")
    mobile_raw = raw.get("mobile")
    return CapabilitiesConfig(mobile=_parse_mobile_capabilities(mobile_raw))


def _parse_backup_section(raw: object) -> BackupConfig:
    if raw is None:
        return BackupConfig()
    if not isinstance(raw, dict):
        raise ValueError("config.backup must be an object")
    defaults = BackupConfig()
    schedule = str(raw.get("schedule", defaults.schedule)).strip() or defaults.schedule
    try:
        retention = int(raw.get("retention_count", defaults.retention_count))
    except (TypeError, ValueError) as exc:
        raise ValueError("config.backup.retention_count must be an integer") from exc
    if retention < 1:
        raise ValueError("config.backup.retention_count must be >= 1")
    return BackupConfig(
        auto_enabled=bool(raw.get("auto_enabled", defaults.auto_enabled)),
        schedule=schedule,
        retention_count=retention,
        include_config=bool(raw.get("include_config", defaults.include_config)),
        include_workspaces=bool(raw.get("include_workspaces", defaults.include_workspaces)),
        include_skill_packages=bool(
            raw.get("include_skill_packages", defaults.include_skill_packages)
        ),
        include_plugins=bool(raw.get("include_plugins", defaults.include_plugins)),
        include_knowledge=bool(raw.get("include_knowledge", defaults.include_knowledge)),
        include_chats=bool(raw.get("include_chats", defaults.include_chats)),
    )


def upload_mb_to_bytes(mb: int) -> int:
    return int(mb) * 1024 * 1024


def parse_max_upload_mb(raw: object, *, default: int = DEFAULT_MAX_UPLOAD_MB) -> int:
    """Coerce a config/env value to a sane megabyte limit."""
    mb: int
    if isinstance(raw, bool) or raw is None:
        logger.warning("max_upload_mb is not int; using %s", default)
        return default
    if isinstance(raw, int):
        mb = raw
    elif isinstance(raw, str):
        try:
            mb = int(raw.strip())
        except ValueError:
            logger.warning("max_upload_mb is not int; using %s", default)
            return default
    else:
        logger.warning("max_upload_mb is not int; using %s", default)
        return default
    if mb < MIN_MAX_UPLOAD_MB:
        logger.warning("max_upload_mb %s is < %s; using %s", mb, MIN_MAX_UPLOAD_MB, default)
        return default
    if mb > MAX_MAX_UPLOAD_MB:
        logger.warning(
            "max_upload_mb %s exceeds %s; clamping",
            mb,
            MAX_MAX_UPLOAD_MB,
        )
        return MAX_MAX_UPLOAD_MB
    return mb


def _coerce_int(name: str, value: str, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        # Do not log the raw env value — names like OCTOP_*_PASSWORD can flow here.
        logger.warning("env %s is not int; using %s", name, default)
        return default


def _coerce_bool(name: str, value: str, default: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    # Do not log the raw env value — OCTOP_REQUIRE_SETUP_PASSWORD is classified as
    # sensitive by CodeQL (py/clear-text-logging-sensitive-data).
    logger.warning("env %s is not bool; using %s", name, default)
    return default


def _parse_database_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in ("postgresql", "postgres"):
        msg = f"OCTOP_DATABASE_URL scheme must be postgresql, got {parsed.scheme!r}"
        raise ValueError(msg)
    if not parsed.hostname:
        raise ValueError("OCTOP_DATABASE_URL missing host")
    if not parsed.path or parsed.path == "/":
        raise ValueError("OCTOP_DATABASE_URL missing database name in path")
    out: dict[str, Any] = {
        "driver": "postgresql",
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
        "url": url,
    }
    if parsed.username:
        out["user"] = parsed.username
    if parsed.password:
        out["password"] = parsed.password
    return out


def _parse_database_section(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        msg = f"config.database must be an object, got {type(raw).__name__}"
        raise ValueError(msg)
    return raw


def parse_database_config(merged: dict[str, Any]) -> DatabaseConfig:
    """Validate and build ``DatabaseConfig`` from a merged dict (file + env)."""
    driver = str(merged.get("driver", "sqlite")).strip().lower()
    if driver not in _VALID_DRIVERS:
        allowed = ", ".join(sorted(_VALID_DRIVERS))
        msg = f"database.driver must be one of {allowed}, got {driver!r}"
        raise ValueError(msg)

    if driver == "sqlite":
        sqlite_path = str(merged.get("sqlite_path", "octop.db")).strip()
        if not sqlite_path:
            raise ValueError("database.sqlite_path must not be empty")
        return DatabaseConfig(driver=driver, sqlite_path=sqlite_path)

    host = str(merged.get("host", "")).strip()
    database = str(merged.get("database", "")).strip()
    user = str(merged.get("user", "")).strip()
    port = int(merged.get("port", 5432))
    if not host:
        raise ValueError("database.host is required for postgresql")
    if not database:
        raise ValueError("database.database is required for postgresql")
    if not user:
        raise ValueError("database.user is required for postgresql")
    if not (1 <= port <= 65535):
        raise ValueError(f"database.port must be 1-65535, got {port}")

    password = merged.get("password")
    if password is not None:
        password = str(password)
        if not password:
            password = None

    raw_url = merged.get("url")
    url = str(raw_url).strip() if raw_url is not None and str(raw_url).strip() else None

    return DatabaseConfig(
        driver=driver,
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        url=url,
    )


def _apply_database_env(merged_db: dict[str, Any]) -> dict[str, Any]:
    out = dict(merged_db)
    if v := os.environ.get("OCTOP_DATABASE_URL"):
        out.update(_parse_database_url(v))
    if v := os.environ.get("OCTOP_DATABASE_DRIVER"):
        out["driver"] = v.strip().lower()
    if v := os.environ.get("OCTOP_DATABASE_SQLITE_PATH"):
        out["sqlite_path"] = v
    if v := os.environ.get("OCTOP_DATABASE_HOST"):
        out["host"] = v
    if v := os.environ.get("OCTOP_DATABASE_PORT"):
        out["port"] = _coerce_int("OCTOP_DATABASE_PORT", v, int(out.get("port", 5432)))
    if v := os.environ.get("OCTOP_DATABASE_NAME"):
        out["database"] = v
    if v := os.environ.get("OCTOP_DATABASE_USER"):
        out["user"] = v
    if v := os.environ.get("OCTOP_DATABASE_PASSWORD"):
        out["password"] = v
    return out


def env_bind_overrides() -> tuple[str | None, int | None]:
    """``OCTOP_BIND_HOST`` / ``OCTOP_PORT`` overrides shared by config and CLI.

    An invalid ``OCTOP_PORT`` logs a warning and yields no override, so the
    file/default value wins — same fallback semantics as ``load_config``.
    """
    host = os.environ.get("OCTOP_BIND_HOST") or None
    port: int | None = None
    if v := os.environ.get("OCTOP_PORT"):
        try:
            port = int(v)
        except ValueError:
            # Do not log the raw env value.
            logger.warning("env %s is not int; ignoring override", "OCTOP_PORT")
        # ``resolve_bind`` reads port 0 as "let the OS pick a free port", so the
        # bindable range here is 0-65535. Anything outside it outranks a valid
        # config.json port and kills startup inside ``socket.bind`` with a bare
        # ``OverflowError``, which names neither the key nor the value.
        if port is not None and not 0 <= port <= 65535:
            logger.warning("env %s is out of range; ignoring override", "OCTOP_PORT")
            port = None
    return host, port


def _publish_default_config(path: Path, defaults: dict[str, Any]) -> bool:
    """Publish a complete sibling file without ever replacing a competing writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive staging creation plus same-filesystem hard-link publication:
    # readers never see partially written JSON, and EEXIST means reread winner.
    # Unsupported filesystems fail closed rather than falling back to overwrite.
    staging: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as output:
            staging = output.name
            json.dump(defaults, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(staging, path)
        except FileExistsError:
            return False
        return True
    finally:
        if staging is not None:
            Path(staging).unlink(missing_ok=True)


def _finish_default_config_publication(path: Path) -> None:
    """Remove only reserved publisher staging names still linked to this config.

    A crash after link() can strand the publisher's second name. This is not
    permission to accept arbitrary shared configs: other aliases stay intact
    and the setup guard still requires st_nlink == 1. Same-UID hostile path
    replacement is outside this protocol, as with the setup mutex itself.
    """
    current = path.lstat()
    if not stat.S_ISREG(current.st_mode) or current.st_nlink <= 1:
        return
    identity = (current.st_dev, current.st_ino)
    prefix = f".{path.name}."
    for candidate in path.parent.iterdir():
        name = candidate.name
        if not name.startswith(prefix) or not name.endswith(".tmp"):
            continue
        random_part = name[len(prefix) : -4]
        # NamedTemporaryFile's reserved publisher namespace; don't sweep other
        # tmp files, directories, symlinks, or links to another config inode.
        if len(random_part) != 8 or any(
            c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in random_part
        ):
            continue
        try:
            entry = candidate.lstat()
            still_current = path.lstat()
        except FileNotFoundError:
            continue
        if (still_current.st_dev, still_current.st_ino) != identity:
            return
        if (
            stat.S_ISREG(entry.st_mode)
            and (entry.st_dev, entry.st_ino) == identity
            and (
                os.name != "posix"
                or (entry.st_uid == os.geteuid() and stat.S_IMODE(entry.st_mode) == 0o600)
            )
        ):
            candidate.unlink(missing_ok=True)


def load_config(path: Path) -> OctopConfig:
    """Load config, publishing defaults atomically only if still absent.

    A competing creator wins without being overwritten. Corrupt files fail
    closed and errors never echo contents (which can contain credentials).
    File symlinks are unsupported; directory aliases remain supported.
    """
    file_defaults = _defaults_for_file()
    created = False
    if path.is_symlink():
        raise ValueError("config.json file symlinks are unsupported; use a private config file")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        created = _publish_default_config(path, file_defaults)
        if path.is_symlink():
            raise ValueError(
                "config.json file symlinks are unsupported; use a private config file"
            ) from None
        text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno});"
            " fix it and retry — no settings were changed"
        ) from exc
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path} must contain a JSON object, got {type(raw).__name__};"
            " fix it and retry — no settings were changed"
        )
    _finish_default_config_publication(path)
    # Preserve first-install deferred binding only for our own unchanged defaults.
    database_in_file = "database" in raw and not (created and raw == file_defaults)

    merged = {**file_defaults, **raw}
    # Legacy key: prefer default_timezone when both are present in the file.
    if "default_timezone" not in raw and "cron_timezone" in raw:
        merged["default_timezone"] = raw["cron_timezone"]
    merged.pop("cron_timezone", None)

    merged_db = {
        **file_defaults["database"],
        **_parse_database_section(raw.get("database")),
    }
    merged_db = _apply_database_env(merged_db)

    env_host, env_port = env_bind_overrides()
    if env_host is not None:
        merged["bind_host"] = env_host
    if env_port is not None:
        merged["port"] = env_port
    if v := os.environ.get("OCTOP_LOG_LEVEL"):
        merged["log_level"] = v
    if v := os.environ.get("OCTOP_ACCESS_TOKEN_TTL"):
        merged["access_token_ttl_seconds"] = _coerce_int(
            "OCTOP_ACCESS_TOKEN_TTL", v, merged["access_token_ttl_seconds"]
        )
    if v := os.environ.get("OCTOP_SESSION_ABSOLUTE_TIMEOUT"):
        merged["session_absolute_timeout_seconds"] = _coerce_int(
            "OCTOP_SESSION_ABSOLUTE_TIMEOUT", v, merged["session_absolute_timeout_seconds"]
        )
    if v := os.environ.get("OCTOP_SESSION_IDLE_TIMEOUT"):
        merged["session_idle_timeout_seconds"] = _coerce_int(
            "OCTOP_SESSION_IDLE_TIMEOUT", v, merged["session_idle_timeout_seconds"]
        )
    if v := os.environ.get("OCTOP_LOGIN_MAX_ATTEMPTS"):
        merged["login_max_attempts"] = _coerce_int(
            "OCTOP_LOGIN_MAX_ATTEMPTS", v, merged.get("login_max_attempts", 5)
        )
    if v := os.environ.get("OCTOP_LOGIN_LOCKOUT_SECONDS"):
        merged["login_lockout_seconds"] = _coerce_int(
            "OCTOP_LOGIN_LOCKOUT_SECONDS", v, merged.get("login_lockout_seconds", 900)
        )
    if (v := os.environ.get("OCTOP_DEFAULT_TIMEZONE")) or (
        v := os.environ.get("OCTOP_CRON_TIMEZONE")
    ):
        merged["default_timezone"] = v
    if v := os.environ.get("OCTOP_CORS_ORIGINS"):
        merged["cors_origins"] = [s.strip() for s in v.split(",") if s.strip()]
    if v := os.environ.get("OCTOP_ENABLE_DASHBOARD"):
        merged["enable_dashboard"] = _coerce_bool(
            "OCTOP_ENABLE_DASHBOARD", v, bool(merged["enable_dashboard"])
        )
    if v := os.environ.get("OCTOP_ENABLE_API_DOCS"):
        merged["enable_api_docs"] = _coerce_bool(
            "OCTOP_ENABLE_API_DOCS", v, bool(merged["enable_api_docs"])
        )
    if v := os.environ.get("OCTOP_REQUIRE_SETUP_PASSWORD"):
        merged["require_setup_password"] = _coerce_bool(
            "OCTOP_REQUIRE_SETUP_PASSWORD", v, bool(merged["require_setup_password"])
        )
    if v := os.environ.get("OCTOP_MAX_UPLOAD_MB"):
        merged["max_upload_mb"] = parse_max_upload_mb(
            v, default=int(merged.get("max_upload_mb", DEFAULT_MAX_UPLOAD_MB))
        )
    else:
        merged["max_upload_mb"] = parse_max_upload_mb(
            merged.get("max_upload_mb", DEFAULT_MAX_UPLOAD_MB)
        )
    if v := os.environ.get("OCTOP_BROWSER_IDLE_TIMEOUT_MINUTES"):
        merged["browser_idle_timeout_minutes"] = _coerce_int(
            "OCTOP_BROWSER_IDLE_TIMEOUT_MINUTES",
            v,
            int(
                merged.get(
                    "browser_idle_timeout_minutes",
                    DEFAULT_BROWSER_IDLE_TIMEOUT_MINUTES,
                )
            ),
        )

    capabilities = _parse_capabilities_section(raw.get("capabilities"))
    if v := os.environ.get("OCTOP_ENABLE_MOBILE"):
        forced = _coerce_bool("OCTOP_ENABLE_MOBILE", v, capabilities.mobile.enabled)
        capabilities = CapabilitiesConfig(
            mobile=MobileCapabilities(
                enabled=forced,
                backend=capabilities.mobile.backend,
                probed_at=capabilities.mobile.probed_at,
                reason=capabilities.mobile.reason,
            )
        )

    capabilities = _parse_capabilities_section(raw.get("capabilities"))
    if v := os.environ.get("OCTOP_ENABLE_MOBILE"):
        forced = _coerce_bool("OCTOP_ENABLE_MOBILE", v, capabilities.mobile.enabled)
        capabilities = CapabilitiesConfig(
            mobile=MobileCapabilities(
                enabled=forced,
                backend=capabilities.mobile.backend,
                probed_at=capabilities.mobile.probed_at,
                reason=capabilities.mobile.reason,
            )
        )

    backup = _parse_backup_section(raw.get("backup"))
    if v := os.environ.get("OCTOP_BACKUP_AUTO_ENABLED"):
        backup = replace(
            backup,
            auto_enabled=_coerce_bool("OCTOP_BACKUP_AUTO_ENABLED", v, backup.auto_enabled),
        )
    if v := os.environ.get("OCTOP_BACKUP_SCHEDULE"):
        schedule = v.strip() or backup.schedule
        backup = replace(backup, schedule=schedule)
    if v := os.environ.get("OCTOP_BACKUP_RETENTION_COUNT"):
        retention = _coerce_int("OCTOP_BACKUP_RETENTION_COUNT", v, backup.retention_count)
        if retention < 1:
            logger.warning(
                "env OCTOP_BACKUP_RETENTION_COUNT is < 1; using %s",
                backup.retention_count,
            )
            retention = backup.retention_count
        backup = replace(backup, retention_count=retention)
    if v := os.environ.get("OCTOP_BACKUP_INCLUDE_CONFIG"):
        backup = replace(
            backup,
            include_config=_coerce_bool("OCTOP_BACKUP_INCLUDE_CONFIG", v, backup.include_config),
        )
    if v := os.environ.get("OCTOP_BACKUP_INCLUDE_WORKSPACES"):
        backup = replace(
            backup,
            include_workspaces=_coerce_bool(
                "OCTOP_BACKUP_INCLUDE_WORKSPACES", v, backup.include_workspaces
            ),
        )
    if v := os.environ.get("OCTOP_BACKUP_INCLUDE_SKILL_PACKAGES"):
        backup = replace(
            backup,
            include_skill_packages=_coerce_bool(
                "OCTOP_BACKUP_INCLUDE_SKILL_PACKAGES",
                v,
                backup.include_skill_packages,
            ),
        )
    if v := os.environ.get("OCTOP_BACKUP_INCLUDE_PLUGINS"):
        backup = replace(
            backup,
            include_plugins=_coerce_bool("OCTOP_BACKUP_INCLUDE_PLUGINS", v, backup.include_plugins),
        )
    if v := os.environ.get("OCTOP_BACKUP_INCLUDE_KNOWLEDGE"):
        backup = replace(
            backup,
            include_knowledge=_coerce_bool(
                "OCTOP_BACKUP_INCLUDE_KNOWLEDGE", v, backup.include_knowledge
            ),
        )
    if v := os.environ.get("OCTOP_BACKUP_INCLUDE_CHATS"):
        backup = replace(
            backup,
            include_chats=_coerce_bool("OCTOP_BACKUP_INCLUDE_CHATS", v, backup.include_chats),
        )

    return OctopConfig(
        bind_host=merged["bind_host"],
        port=int(merged["port"]),
        log_level=merged["log_level"],
        access_token_ttl_seconds=int(merged["access_token_ttl_seconds"]),
        session_absolute_timeout_seconds=int(merged["session_absolute_timeout_seconds"]),
        session_idle_timeout_seconds=int(merged["session_idle_timeout_seconds"]),
        login_max_attempts=int(merged.get("login_max_attempts", 5)),
        login_lockout_seconds=int(merged.get("login_lockout_seconds", 900)),
        cors_origins=list(merged.get("cors_origins") or []),
        default_timezone=str(merged.get("default_timezone") or "Asia/Shanghai"),
        enable_dashboard=bool(merged["enable_dashboard"]),
        enable_api_docs=bool(merged["enable_api_docs"]),
        history_v2_enabled=_coerce_bool(
            "OCTOP_HISTORY_V2_ENABLED",
            os.environ.get(
                "OCTOP_HISTORY_V2_ENABLED", str(merged.get("history_v2_enabled", False))
            ),
            False,
        ),
        require_setup_password=bool(merged["require_setup_password"]),
        user_authentication_mode=os.environ.get(
            "OCTOP_USER_AUTHENTICATION_MODE", str(merged["user_authentication_mode"])
        ),
        unified_oidc_issuer=os.environ.get(
            "OCTOP_UNIFIED_OIDC_ISSUER", str(merged["unified_oidc_issuer"])
        ),
        database=parse_database_config(merged_db),
        database_in_file=database_in_file,
        tls=_parse_tls_section(raw.get("tls")),
        backup=backup,
        capabilities=capabilities,
        max_upload_mb=int(merged.get("max_upload_mb", DEFAULT_MAX_UPLOAD_MB)),
        browser_idle_timeout_minutes=max(
            0,
            int(
                merged.get(
                    "browser_idle_timeout_minutes",
                    DEFAULT_BROWSER_IDLE_TIMEOUT_MINUTES,
                )
            ),
        ),
    )
