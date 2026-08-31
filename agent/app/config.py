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


def _bypass_proxy_for_llm() -> None:
    """Route LLM traffic directly, bypassing any inherited HTTP(S)_PROXY.

    Shell proxy env (e.g. a local Clash on :7897) leaks into the service
    process. Observed on 2026-08-31: small requests pass but large-payload
    LLM calls hang for minutes through the proxy. bigmodel.cn is directly
    reachable, so we add the LLM host to no_proxy/NO_PROXY at import time -
    before any httpx client is created.
    """
    if not LLM_BASE_URL:
        return
    try:
        from urllib.parse import urlparse

        host = urlparse(LLM_BASE_URL).hostname
    except ValueError:
        return
    if not host:
        return
    for var in ("no_proxy", "NO_PROXY"):
        current = os.environ.get(var, "")
        entries = [e.strip() for e in current.split(",") if e.strip()]
        if host not in entries:
            entries.append(host)
            os.environ[var] = ",".join(entries)


_bypass_proxy_for_llm()


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
