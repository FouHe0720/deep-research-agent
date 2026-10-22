from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RESEARCH_", extra="ignore")
    data_dir: Path = Path(".runtime")
    max_jobs: int = Field(2, ge=1, le=4)
    max_queue: int = Field(20, ge=1, le=100)
    allow_live: bool = False
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "::1", "testserver"]
    )
    api_key: SecretStr | None = None
    web_api_key: SecretStr | None = None
    github_token: SecretStr | None = None
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_kind: str = "openai"
    llm_input_usd_per_million: float | None = Field(None, ge=0)
    llm_output_usd_per_million: float | None = Field(None, ge=0)
    gateway_url: str | None = None
    gateway_api_key: SecretStr | None = None
    rag_url: str | None = None

    @model_validator(mode="after")
    def validate_llm(self):
        if self.llm_kind not in {"openai", "gateway"}:
            raise ValueError("RESEARCH_LLM_KIND must be openai or gateway")
        return self

    @property
    def database(self) -> Path:
        return self.data_dir / "research.db"
