import os

AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "").strip() or None
AGENT_HOST = os.environ.get("AGENT_HOST", "0.0.0.0")
AGENT_PORT = int(os.environ.get("AGENT_PORT", "5060"))

# LLM: any OpenAI-compatible endpoint (GLM / DeepSeek / OpenAI / vLLM ...)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").strip()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()

# OpenNotebook REST API
ON_BASE_URL = os.environ.get("ON_BASE_URL", "http://localhost:5055").rstrip("/")
ON_API_TOKEN = os.environ.get("ON_API_TOKEN", "").strip()
ON_TIMEOUT = float(os.environ.get("ON_TIMEOUT", "120"))


def require_llm_config() -> None:
    missing = [
        name
        for name, val in (
            ("LLM_BASE_URL", LLM_BASE_URL),
            ("LLM_API_KEY", LLM_API_KEY),
            ("LLM_MODEL", LLM_MODEL),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required LLM configuration: {', '.join(missing)}. "
            "Set them in the environment or .env file (see .env.example)."
        )
