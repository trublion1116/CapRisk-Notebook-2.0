import os

from langchain_openai import ChatOpenAI

from app import config

# Per-call timeout: without one, a dropped connection can hang a tool call for
# hours (observed via Langfuse tracing on 2026-08-29) before surfacing as
# "Connection error.".
DEFAULT_TIMEOUT = 300.0
# Retries kept low on purpose: during a provider rate-limit cooldown the
# OpenAI SDK's retry loop keeps hitting the endpoint and self-sustains the
# cooldown (observed with GLM 1302 on 2026-08-31). Fail fast instead.
DEFAULT_MAX_RETRIES = 1


def build_llm(max_tokens: int = 8192, temperature: float = 0.3) -> ChatOpenAI:
    """Build the chat model from OpenAI-compatible env configuration."""
    config.require_llm_config()
    return ChatOpenAI(
        model=config.LLM_MODEL,
        base_url=config.LLM_BASE_URL or None,
        api_key=config.LLM_API_KEY or None,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=float(os.environ.get("LLM_TIMEOUT", DEFAULT_TIMEOUT)),
        max_retries=int(os.environ.get("LLM_MAX_RETRIES", DEFAULT_MAX_RETRIES)),
    )
