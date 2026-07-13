from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RADCLIFFE_", env_file=".env", extra="ignore")

    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"
    bootstrap_token: str = Field(default="local-development-only", min_length=16)
    internal_token: str = Field(default="local-internal-only", min_length=16)
    database_path: Path = Path("var/radcliffe.sqlite3")
    source_root: Path = Path("fixtures/sources")
    room_token_ttl_seconds: int = Field(default=600, ge=60, le=3600)
