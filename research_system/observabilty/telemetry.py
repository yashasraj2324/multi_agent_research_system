import os
from typing import Optional
import logfire
from .config import ObservabilityConfig

_IS_INSTRUMENTED = False


def setup_observability(
    config: Optional[ObservabilityConfig] = None,
) -> ObservabilityConfig:
    """Configures Logfire and OpenTelemetry tracing based on the provided configuration."""
    global _IS_INSTRUMENTED
    if config is None:
        config = ObservabilityConfig()

    if _IS_INSTRUMENTED:
        return config

    # 1. Resolve Configuration & Environment
    os.environ["OTEL_EXPORTER_OTLP_TIMEOUT"] = str(config.otlp_timeout_ms)

    if config.otlp_endpoint:
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = config.otlp_endpoint
    elif "OTEL_EXPORTER_OTLP_ENDPOINT" in os.environ:
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)

    # 2. Configure Logfire with direct cloud uploading & 3. Live terminal tracing
    logfire.configure(
        service_name=config.service_name,
        send_to_logfire=config.send_to_logfire,  # True: streams live to Logfire Cloud!
        console=logfire.ConsoleOptions(
            colors="auto"
        )  # Live colored terminal output
        if config.console_enabled
        else False,
    )

    # 4. Global Auto-Instrumentation
    from opentelemetry.instrumentation.logging import LoggingInstrumentor

    logfire.instrument_openai()
    logfire.instrument_pydantic()
    logfire.instrument_pydantic_ai()
    logfire.instrument_httpx()
    logfire.instrument_requests()
    logfire.instrument_pymongo()
    LoggingInstrumentor().instrument(set_logging_format=True)

    _IS_INSTRUMENTED = True
    return config