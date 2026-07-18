from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RADCLIFFE_", env_file=".env", extra="ignore")

    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"
    bootstrap_token: str = Field(default="local-development-only", min_length=16)
    internal_token: str = Field(default="local-internal-only", min_length=16)
    owner_id: str = Field(default="local-owner", pattern=r"^[A-Za-z0-9_.:-]{1,96}$")
    database_path: Path = Path("var/radcliffe.sqlite3")
    source_root: Path = Path("fixtures/sources")
    sidecar_url: str = "http://127.0.0.1:8787"
    room_token_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    managed_workers_enabled: bool = True
    worker_idle_seconds: int = Field(default=1800, ge=60)
    worker_reconcile_seconds: int = Field(default=10, ge=1)
    worker_stop_timeout_seconds: int = Field(default=5, ge=1, le=30)
    worker_uv_bin: Path = Path("/opt/homebrew/bin/uv")
    worker_workspace_root: Path = Field(default_factory=_workspace_root)
    worker_log_directory: Path = Path("var/workers")
