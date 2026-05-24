from enum import Enum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class FlareEnv(str, Enum):
    LOCAL = "local"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    flare_env: FlareEnv = FlareEnv.LOCAL

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/flare"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Auth
    secret_key: str = "change-me-in-production"
    github_client_id: str = ""
    github_client_secret: str = ""
    github_org: str = "BlazeUp-AI"

    # GitHub API (for ref resolution, PR comments, org membership)
    github_token: str = ""
    github_repo_owner: str = "BlazeUp-AI"
    github_repo_name: str = "Observal"
    github_webhook_secret: str = ""

    # AWS
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"

    # Terraform (AWS)
    terraform_state_bucket: str = "flare-terraform-state"
    terraform_lock_table: str = "flare-terraform-locks"

    # GCP
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"
    gcp_zone: str = "us-central1-a"
    gcp_terraform_state_bucket: str = ""

    # Route53
    route53_zone_id: str = ""
    site_base_domain: str = "observal.io"

    # Flare public URL
    flare_base_url: str = "https://flare.observal.io"

    # Mock overrides (default to None = follow FLARE_ENV)
    mock_github: bool | None = None
    mock_terraform: bool | None = None
    mock_ssm: bool | None = None
    mock_compute: bool | None = None

    @property
    def is_local(self) -> bool:
        return self.flare_env == FlareEnv.LOCAL

    @property
    def use_mock_github(self) -> bool:
        return self.mock_github if self.mock_github is not None else self.is_local

    @property
    def use_mock_terraform(self) -> bool:
        return self.mock_terraform if self.mock_terraform is not None else self.is_local

    @property
    def use_mock_ssm(self) -> bool:
        return self.mock_ssm if self.mock_ssm is not None else self.is_local

    @property
    def use_mock_compute(self) -> bool:
        return self.mock_compute if self.mock_compute is not None else self.is_local


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.is_local and settings.secret_key == "change-me-in-production":
        raise RuntimeError("FATAL: secret_key must be changed from the default in production")
    return settings
