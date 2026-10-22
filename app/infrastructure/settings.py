from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    github_webhook_secret: str = "change-me"
    github_app_id: str | None = None
    github_private_key_path: Path | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_app_slug: str | None = None
    agent_qc_public_url: str = "http://127.0.0.1:8000"
    agent_qc_tda_enabled: bool = True
    agent_qc_generated_pytest_enabled: bool = True
    agent_qc_tda_workspace_enabled: bool = True
    agent_qc_tda_max_inquiry_turns: int = Field(default=6,ge=2,le=20)
    agent_qc_tda_max_cases: int = Field(default=100,ge=1,le=200)
    agent_qc_tda_max_rounds: int = Field(default=3,ge=1,le=5)
    agent_qc_tda_max_output_tokens: int = Field(default=16000,ge=1000,le=32000)
    agent_qc_tda_reference_execution: bool = False
    agent_qc_tda_runners: str = 'http,unit,browser,k6,ai'
    agent_qc_test_isolation: Literal['docker','local'] = 'docker'
    agent_qc_k6_binary: Path = Path('.tools/k6/k6.exe')
    agent_qc_browser_channel: str = 'chrome'
    agent_qc_tda_dir: Path = Path(".data/test-design")
    agent_qc_tda_model: str | None = None
    tda_openai_api_key: str | None = None
    agent_qc_tda_reasoning_effort: str | None = "low"
    agent_qc_tda_max_calls: int = 24
    agent_qc_tda_timeout_seconds: int = Field(default=180,ge=10,le=600)
    agent_qc_tda_max_input_chars: int = Field(default=60000,ge=12000,le=200000)
    agent_qc_tda_max_source_bytes: int = 160000
    agent_qc_data_dir: Path = Path(".data")
    # Runtime clones must live outside the source tree watched by uvicorn --reload.
    agent_qc_workspace_dir: Path = Path("../.agent-qc-workspaces")
    agent_qc_dry_run: bool = True
    agent_qc_publish_pr_review: bool = True
    agent_qc_agent_enabled: bool = True
    openai_api_key: str | None = None
    openai_model: str = "gpt-6-astra"
    agent_qc_case_max_calls:int=Field(default=12,ge=2,le=100)
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
