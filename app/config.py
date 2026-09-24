from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_webhook_secret: str = "change-me"
    github_app_id: str | None = None
    github_private_key_path: Path | None = None
    agent_qc_data_dir: Path = Path(".data")
    # Runtime clones must live outside the source tree watched by uvicorn --reload.
    agent_qc_workspace_dir: Path = Path("../.agent-qc-workspaces")
    agent_qc_dry_run: bool = True
    agent_qc_worker_command: str = "python -m pytest -q"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
