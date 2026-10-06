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
    agent_qc_publish_pr_review: bool = True
    agent_qc_agent_enabled: bool = True
    openai_api_key: str | None = None
    openai_model: str = "gpt-6-astra"
    agent_qc_external_agents_file: Path | None = Path("external-agents.yaml")
    agent_qc_execution_backend: str = "local"
    agent_qc_docker_images_file: Path | None = Path("docker-images.yaml")
    agent_qc_manual_api_token: str | None = None
    agent_qc_manual_allowlist: str = ""
    agent_qc_local_target_enabled: bool = True
    triage_min_conf: float = 0.9
    triage_max_confirmations: int = 1
    planner_max_diff_tokens: int = 20000
    agent_qc_org_policy_file: Path = Path("org-policy.yaml")
    agent_qc_artifact_retention_days: int = 30
    agent_qc_schedule_enabled: bool = False
    agent_qc_deployment_trigger_enabled: bool = False
    agent_qc_artifact_dir: Path = Path(".data/artifacts")
    agent_qc_worker_concurrency: int = 4

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
