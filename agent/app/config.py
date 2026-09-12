import os

AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "").strip() or None
AGENT_HOST = os.environ.get("AGENT_HOST", "0.0.0.0")
AGENT_PORT = int(os.environ.get("AGENT_PORT", "5060"))

# LLM: any OpenAI-compatible endpoint (GLM / DeepSeek / OpenAI / vLLM ...)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").strip()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()

# Vision（图表解读）：独立于文本 LLM 配置——文本主模型可能无视觉能力
# （如 deepseek-v4-flash），图表分析走专门的 VLM 端点（bigmodel glm-4.6v，
# OpenAI 兼容）。缺省回退到 LLM_* 配置（若文本模型本身支持视觉）。
VISION_BASE_URL = os.environ.get("VISION_BASE_URL", "").strip() or LLM_BASE_URL
VISION_API_KEY = os.environ.get("VISION_API_KEY", "").strip() or LLM_API_KEY
VISION_MODEL = os.environ.get("VISION_MODEL", "").strip() or LLM_MODEL

# OpenNotebook REST API
ON_BASE_URL = os.environ.get("ON_BASE_URL", "http://localhost:5055").rstrip("/")
ON_API_TOKEN = os.environ.get("ON_API_TOKEN", "").strip()
ON_TIMEOUT = float(os.environ.get("ON_TIMEOUT", "120"))

# 图表图片在见解报告里的引用 base。默认空 = 相对路径 /agent-images/...，
# 由 ON 前端的 Next rewrite 同源代理到本服务（浏览器不直连 5060，
# Windows→WSL 转发不可靠）。仅当前端无法配置代理时才显式设为
# http://localhost:5060 之类的绝对地址。
AGENT_PUBLIC_URL = os.environ.get("AGENT_PUBLIC_URL", "").strip()

def _bypass_proxy_for_llm() -> None:
    """Opt-in: route LLM traffic directly (bypassing inherited HTTP(S)_PROXY).

    History (do not flip the default casually):
    - 2026-08-31: shell proxy leaked in, large LLM payloads hung through it;
      no_proxy bypass was added for the LLM host.
    - 2026-09-11: environment switched to Clash TUN (fake-ip DNS). LLM hosts
      now resolve to 198.18.x.x fake IPs - a direct connection to a fake IP
      hangs, while going through the proxy port works. The bypass became
      harmful, so it is now opt-in via LLM_BYPASS_PROXY=1.
    """
    if os.environ.get("LLM_BYPASS_PROXY", "").strip() != "1":
        return

    from urllib.parse import urlparse

    hosts = []
    for base in (LLM_BASE_URL, VISION_BASE_URL):
        if not base:
            continue
        try:
            host = urlparse(base).hostname
        except ValueError:
            continue
        if host and host not in hosts:
            hosts.append(host)
    for host in hosts:
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
