"""Environment-driven configuration (``.env`` for local development)."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


def _csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Azure
    azure_docintel_endpoint: str
    azure_docintel_key: SecretStr
    azure_docintel_model_id: str = "prebuilt-read"
    analysis_timeout_seconds: float = Field(120, gt=0, le=900)
    max_concurrent_analyses: int = Field(4, ge=1, le=64)

    # Input limits
    max_file_size_mb: float = Field(20, gt=0, le=500)

    # URL fetching
    url_allowed_domains: CsvList = Field(default_factory=list)
    url_fetch_timeout_seconds: float = Field(30, gt=0, le=300)
    url_max_redirects: int = Field(3, ge=0, le=10)

    # Server
    # streamable-http (default), sse (legacy, long-lived stream + POST channel), or stdio (local subprocess)
    mcp_transport: Literal["streamable-http", "sse", "stdio"] = "streamable-http"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(8000, ge=1, le=65535)
    mcp_path: str = "/mcp"  # streamable-http endpoint
    mcp_sse_path: str = "/sse"  # sse stream endpoint (clients POST to /messages/)
    mcp_auth_token: SecretStr | None = None
    mcp_allowed_hosts: CsvList = Field(default_factory=list)
    mcp_allowed_origins: CsvList = Field(default_factory=list)
    log_level: str = "INFO"

    _split_lists = field_validator(
        "url_allowed_domains", "mcp_allowed_hosts", "mcp_allowed_origins", mode="before"
    )(_csv)

    @field_validator("azure_docintel_endpoint")
    @classmethod
    def _endpoint_https(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("AZURE_DOCINTEL_ENDPOINT must be an https:// URL")
        return value.strip()

    @field_validator("azure_docintel_key")
    @classmethod
    def _key_present(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("AZURE_DOCINTEL_KEY must not be empty")
        return value

    @field_validator("mcp_path")
    @classmethod
    def _path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("MCP_PATH must start with '/'")
        return value

    @field_validator("mcp_sse_path")
    @classmethod
    def _sse_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("MCP_SSE_PATH must start with '/'")
        return value

    @field_validator("log_level")
    @classmethod
    def _level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("invalid LOG_LEVEL")
        return value

    @field_validator("mcp_auth_token")
    @classmethod
    def _token_empty_is_none(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            return None
        return value

    @property
    def is_loopback_bind(self) -> bool:
        host = self.mcp_host.strip("[]")
        if host in _LOOPBACK_NAMES:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @model_validator(mode="after")
    def _require_hardening_when_exposed(self) -> Settings:
        if self.mcp_transport != "stdio" and not self.is_loopback_bind:
            if self.mcp_auth_token is None or len(self.mcp_auth_token.get_secret_value()) < 16:
                raise ValueError(
                    "MCP_AUTH_TOKEN (>=16 chars) is required when MCP_HOST is not loopback"
                )
            if not self.mcp_allowed_hosts:
                raise ValueError("MCP_ALLOWED_HOSTS is required when MCP_HOST is not loopback")
        return self

    @property
    def max_file_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
