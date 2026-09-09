"""Application settings, loaded from environment variables or a `.env` file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level application configuration."""

    model_config = SettingsConfigDict(
        env_prefix="LIQUIDITY_HUNTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "liquidity-hunter"
    environment: str = "development"
    log_level: str = "INFO"

    #: Fill `DashboardData.structural_stall` (see
    #: `app.dashboard_data._STRUCTURAL_STALL_ENABLED`). On by default; set
    #: `LIQUIDITY_HUNTER_STRUCTURAL_STALL=0` (or that line in `.env`) to turn
    #: the reading off. It only makes the field be computed -- no event, trend,
    #: line or level changes either way.
    structural_stall: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return a cached `Settings` instance."""
    return Settings()
