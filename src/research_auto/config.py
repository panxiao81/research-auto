from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    database_url: str = Field(..., alias="DATABASE_URL")
    playwright_headless: bool = Field(True, alias="PLAYWRIGHT_HEADLESS")
    worker_poll_seconds: int = Field(5, alias="WORKER_POLL_SECONDS")
    worker_queue: str = Field("all", alias="WORKER_QUEUE")
    storage_backend: str = Field("local", alias="STORAGE_BACKEND")
    artifact_root: str = Field("data/artifacts", alias="ARTIFACT_ROOT")
    s3_bucket: str | None = Field(None, alias="S3_BUCKET")
    s3_prefix: str = Field("papers", alias="S3_PREFIX")
    s3_region: str | None = Field(None, alias="AWS_REGION")
    s3_endpoint_url: str | None = Field(None, alias="AWS_ENDPOINT_URL")
    llm_provider: str = Field("mock", alias="LLM_PROVIDER")
    llm_model: str = Field("gpt-5-mini", alias="LLM_MODEL")
    llm_api_key: str | None = Field(None, alias="LLM_API_KEY")
    llm_base_url: str = Field("https://api.openai.com/v1", alias="LLM_BASE_URL")
    codex_access_token: str | None = Field(None, alias="CODEX_ACCESS_TOKEN")
    codex_account_id: str | None = Field(None, alias="CODEX_ACCOUNT_ID")
    codex_auth_file: str = Field(
        str(Path.home() / ".codex" / "auth.json"), alias="CODEX_AUTH_FILE"
    )
    github_copilot_token: str | None = Field(None, alias="GITHUB_COPILOT_TOKEN")
    github_copilot_base_url: str = Field(
        "https://api.githubcopilot.com", alias="GITHUB_COPILOT_BASE_URL"
    )
    litellm_backend: str | None = Field(None, alias="LITELLM_BACKEND")
    arxiv_contact: str = Field("mailto:pan-xiao@live.cn", alias="ARXIV_CONTACT")
    arxiv_cache_ttl_hours: int = Field(24 * 7, alias="ARXIV_CACHE_TTL_HOURS")
    arxiv_delay_seconds: float = Field(3.0, alias="ARXIV_DELAY_SECONDS")
    arxiv_page_size: int = Field(100, alias="ARXIV_PAGE_SIZE")
    arxiv_num_retries: int = Field(5, alias="ARXIV_NUM_RETRIES")

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


def get_settings() -> Settings:
    return Settings()
