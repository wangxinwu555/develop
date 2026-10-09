from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    agent_mode: Literal["demo", "llm"] = "demo"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = ""
    llm_temperature: float = Field(default=0, ge=0, le=2)
    llm_max_tokens: int = Field(default=1200, ge=1)
    llm_timeout_seconds: float = Field(default=25, ge=1, le=120)
    max_agent_steps: int = Field(default=8, ge=2, le=30)
    retrieval_mode: Literal["vector"] = "vector"
    embedding_provider: Literal["demo", "api"] = "demo"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_base_url: str = ""
    embedding_model: str = ""
    pdf_dir: Path = ROOT / "output/pdf"
    chunk_size: int = Field(default=240, ge=64, le=4000)
    chunk_overlap: int = Field(default=40, ge=0, le=1000)
    retrieval_min_score: float = Field(default=0.10, ge=0, le=1)
    knowledge_dir: Path | None = None
    runtime_dir: Path = ROOT / "runtime"

    @model_validator(mode="after")
    def valid_chunk_overlap(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP 必须小于 CHUNK_SIZE。")
        return self

    def validate_embedding(self) -> None:
        if self.embedding_provider == "api" and (
            not self.embedding_api_key.get_secret_value() or not self.embedding_model
        ):
            raise ValueError("api 向量模式需要配置 EMBEDDING_API_KEY 和 EMBEDDING_MODEL。")

    def validate_provider(self) -> None:
        if self.agent_mode == "llm" and (
            not self.llm_api_key.get_secret_value() or not self.llm_model
        ):
            raise ValueError("llm 模式需要配置 LLM_API_KEY 和 LLM_MODEL。")
        self.validate_embedding()

    @property
    def index_dir(self) -> Path:
        return self.knowledge_dir or self.runtime_dir / "knowledge"

    @property
    def database_path(self) -> Path:
        return self.runtime_dir / "business.sqlite"

    @property
    def checkpoint_path(self) -> Path:
        return self.runtime_dir / "checkpoints.sqlite"
