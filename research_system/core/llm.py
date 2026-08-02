"""Model factory — provides Azure OpenAI configuration."""
from __future__ import annotations

import os

from openai import AsyncAzureOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

# Importing config triggers dotenv loading so env vars are always available
from .config import config as _cfg  # noqa: F401


def provider_info() -> tuple[str, str]:
    """Return (provider, model) for display / logging."""
    return "azure", os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")


def is_configured(provider: str | None = None) -> bool:
    return bool(os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT"))

def get_model() -> OpenAIChatModel:
    """Return a pydantic-ai model for Azure OpenAI."""
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY missing.")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT missing.")
        
    client = AsyncAzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")
    )
    model_name = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(openai_client=client)
    )
