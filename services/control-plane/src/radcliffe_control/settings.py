from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _model_pricing_path() -> Path:
    return _workspace_root() / "services/control-plane/config/model-pricing-v1.json"


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
    telemetry_path: Path = Path("var/telemetry/events.jsonl")
    sidecar_url: str = "http://127.0.0.1:8787"
    model_pricing_path: Path = Field(default_factory=_model_pricing_path)
    resume_representative_output_tokens: int = Field(default=512, ge=0, le=32_768)
    build_revision: str = Field(default="development", pattern=r"^(development|[a-f0-9]{7,64})$")
    room_token_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    managed_workers_enabled: bool = True
    worker_idle_seconds: int = Field(default=1800, ge=60)
    worker_reconcile_seconds: int = Field(default=10, ge=1)
    worker_handoff_grace_seconds: float = Field(default=5, ge=1, le=30)
    worker_stop_timeout_seconds: int = Field(default=5, ge=1, le=30)
    worker_uv_bin: Path = Path("/opt/homebrew/bin/uv")
    worker_workspace_root: Path = Field(default_factory=_workspace_root)
    worker_log_directory: Path = Path("var/workers")
    job_executor_enabled: bool = False
    job_executor_command: str = ""
    job_executor_backend: Literal["custom-loop", "claude-agent-sdk"] = "custom-loop"
    job_model_provider: Literal["stub", "anthropic", "ollama"] = "stub"
    job_model_id: str = Field(default="", pattern=r"^[A-Za-z0-9._:-]{0,128}$")
    job_allow_cloud_models: bool = False
    anthropic_api_key: str = Field(default="", repr=False)
    job_workspace_root: Path = Path("var/jobs")
    job_executor_max_concurrency: int = Field(default=2, ge=1, le=8)
    job_executor_poll_seconds: float = Field(default=0.25, ge=0.05, le=30)
    job_executor_heartbeat_seconds: float = Field(default=5, ge=0.05, le=60)
    job_executor_lease_seconds: int = Field(default=30, ge=1, le=300)
    job_executor_stop_timeout_seconds: float = Field(default=5, ge=0.05, le=30)
    job_executor_max_output_bytes: int = Field(default=1_048_576, ge=1024, le=16_777_216)
    job_executor_max_runtime_seconds: int = Field(default=300, ge=60, le=86_400)
