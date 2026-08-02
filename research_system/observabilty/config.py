from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class ObservabilityConfig(BaseSettings):
    """Configuration settings for Logfire and OpenTelemetry Collector integration."""

    service_name: str = Field(
        default="multi-agent-research",
        description=(
            "Uniquely identifies your application service when traces arrive at "
            "the OTel Collector, preventing logs from mixing with other backends."
        ),
    )

    otlp_endpoint: Optional[str] = Field(
        default=None,
        description=(
            "Optional custom OTel Collector endpoint. Set to None when using direct "
            "Logfire cloud ingestion."
        ),
    )

    send_to_logfire: bool = Field(
        default=True,
        description=(
            "Set to True to automatically authenticate and stream telemetry directly "
            "to Logfire Cloud without needing a running Docker collector."
        ),
    )

    console_enabled: bool = Field(
        default=True,
        description=(
            "Outputs beautiful, indented execution trees directly to your terminal "
            "during development."
        ),
    )

    otlp_timeout_ms: int = Field(
        default=30000,
        description=(
            "Network timeout in milliseconds for OTLP exporters (traces, metrics, logs). "
            "Increased from default 10s (10000) to 30s (30000) to prevent WAN read timeouts "
            "during heavy concurrent multi-agent AI execution."
        ),
    )