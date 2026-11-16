"""Configuration loaded from environment variables."""
import os
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).parent
PACKAGE_DIR = _ROOT.parent   # research_system/
REPO_ROOT = PACKAGE_DIR.parent


def load_env_files(repo_root: Path = REPO_ROOT, package_dir: Path = PACKAGE_DIR) -> None:
    """Load `<repo>/.env`, falling back to `research_system/.env`.

    The root file wins: the package-level file is loaded first and the root file then
    overrides any key it defines, so the package file only supplies keys the root file
    lacks. Values in either file take precedence over already-set environment variables.
    """
    load_dotenv(package_dir / ".env", override=True)
    load_dotenv(repo_root / ".env", override=True)


load_env_files()


class Config:
    # ---- Providers ----
    AZURE_OPENAI_API_KEY: str = os.environ.get("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_ENDPOINT: str = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "azure").lower()
    CHROMA_API_KEY:str = os.environ.get("CHROMA_API_KEY")
    CHROMA_TENANT:str = os.environ.get("CHROMA_TENANT")
    CHROMA_DATABASE:str = os.environ.get("CHROMA_DATABASE", "default")
    # ---- Models ----
    AZURE_OPENAI_DEPLOYMENT_NAME: str = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    AZURE_OPENAI_API_VERSION: str = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")
    MODEL_NAME: str = os.environ.get("LEAD_MODEL", "claude-sonnet-4-6")

    # ---- Tavily ----
    TAVILY_API_KEY: str = os.environ.get("TAVILY_API_KEY", "")

    # ---- Runtime tuning ----
    MAX_SUBAGENTS: int = int(os.environ.get("MAX_SUBAGENTS", "4"))
    MAX_SEARCH_RESULTS_PER_AGENT: int = int(
        os.environ.get("MAX_SEARCH_RESULTS_PER_AGENT", "5")
    )

    # ---- History ----
    HISTORY_DB_PATH: str = os.environ.get(
        "HISTORY_DB_PATH", str(_ROOT / "history.db")
    )

    # ---- Helpers ----
    @classmethod
    def active_provider(cls) -> str:
        return (os.environ.get("LLM_PROVIDER") or cls.LLM_PROVIDER or "azure").lower()

    @classmethod
    def active_model(cls) -> str:
        return cls.AZURE_OPENAI_DEPLOYMENT_NAME

    @classmethod
    def is_llm_configured(cls, provider: str | None = None) -> bool:
        return bool(cls.AZURE_OPENAI_API_KEY and cls.AZURE_OPENAI_ENDPOINT)

    @classmethod
    def is_tavily_configured(cls) -> bool:
        return bool(cls.TAVILY_API_KEY)
    @classmethod
    def is_chromadb_configured(cls)-> bool:
        return bool(cls.CHROMA_API_KEY)

config = Config()
