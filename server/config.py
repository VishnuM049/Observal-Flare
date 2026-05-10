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

    # Terraform
    terraform_state_bucket: str = "flare-terraform-state"
    terraform_lock_table: str = "flare-terraform-locks"

    # Route53
    route53_zone_id: str = ""
    site_base_domain: str = "observal.io"

    # Email (SES)
    ses_from_address: str = "noreply@observal.io"

    # Flare public URL
    flare_base_url: str = "https://flare.observal.io"

    @property
    def is_local(self) -> bool:
        return self.flare_env == FlareEnv.LOCAL


@lru_cache
def get_settings() -> Settings:
    return Settings()
