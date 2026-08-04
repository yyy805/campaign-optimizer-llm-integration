from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_ROLES = {"SERVICE", "REVIEWER", "GOVERNANCE_APPROVER", "PUBLISHER", "ADMIN"}


@dataclass(frozen=True)
class PrincipalConfig:
    key: str
    principal_id: str
    tenant: str
    role: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "demo"
    database_url: str = "sqlite:///./data/review.db"
    ontology_path: Path = Path("../docs/ontology/ontology 概念卡")
    expected_ontology_checksum: str = "a2eaaf287417469a592ecb48d3a31759f930761bc97269e9c61618b7f65ca858"
    final_plan_schema_path: Path = Path("../campaign_optimizer/schemas/final_plan.schema.json")
    ontology_review_schema_path: Path = Path("../campaign_optimizer/schemas/ontology_review.schema.json")
    docs_enabled: bool = True
    cors_origins: list[str] = ["http://localhost:3000"]
    log_level: str = "INFO"
    api_key_principals: str = ""
    plan_review_client_id: str = "demo_client_001"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.lstrip().startswith("["):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    def principals(self) -> dict[str, PrincipalConfig]:
        result: dict[str, PrincipalConfig] = {}
        principal_ids: set[str] = set()
        for raw in self.api_key_principals.split(","):
            parts = [part.strip() for part in raw.split(":")]
            if len(parts) != 4 or not all(parts):
                raise ValueError("API_KEY_PRINCIPALS entries must be key:principal_id:tenant:role")
            principal = PrincipalConfig(*parts)
            if not principal.key.isascii():
                raise ValueError("API keys must contain ASCII characters only")
            if principal.role not in SUPPORTED_ROLES:
                raise ValueError(f"unsupported API principal role: {principal.role}")
            if principal.key in result:
                raise ValueError("duplicate API key configuration")
            if principal.principal_id in principal_ids:
                raise ValueError("principal_id values must be globally unique")
            result[principal.key] = principal
            principal_ids.add(principal.principal_id)
        if not result:
            raise ValueError("at least one API principal is required")
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
