import logging
import os

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app import config
from app.agents import build_chat_agent
from app.chat_stream import stream_chat_turn
from app.jobs import create_job, get_job
from app.runner import run_extraction_job
from app.tracing import new_handler, traced

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Treasury Agent",
    description=(
        "Deepagents-based viewpoint extraction service for the OpenNotebook "
        "treasury risk analysis system."
    ),
)

_chat_agent = None


def get_chat_agent():
    global _chat_agent
    if _chat_agent is None:
        _chat_agent = build_chat_agent()
    return _chat_agent


def check_auth(request: Request) -> None:
    if config.AGENT_API_KEY:
        auth = request.headers.get("Authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or token != config.AGENT_API_KEY:
            raise HTTPException(status_code=401, detail="Invalid or missing token")


def require_llm() -> None:
    try:
        config.require_llm_config()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


class ChatTurnRequest(BaseModel):
    thread_id: str = Field(default="", description="ON chat session id")
    system_prompt: str = Field(
        default="", description="Notebook context system prompt built by ON"
    )
    messages: list[dict] = Field(
        default_factory=list,
        description='Conversation history: [{"role": "human"|"ai", "content": "..."}]',
    )


class ChatTurnResponse(BaseModel):
    content: str


def to_lc_messages(request: ChatTurnRequest) -> list:
    """Convert a proxied chat turn into LangChain messages.

    The chat agent's own system prompt (CHAT_PREAMPT) is set in
    build_chat_agent; here we only inject the notebook context ON built.
    """
    lc_messages: list = []
    if request.system_prompt:
        lc_messages.append(SystemMessage(content=request.system_prompt))
    for m in request.messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if not content:
            continue
        if role in ("human", "user"):
            lc_messages.append(HumanMessage(content=content))
        elif role in ("ai", "assistant"):
            lc_messages.append(AIMessage(content=content))
    return lc_messages


@app.post("/chat", response_model=ChatTurnResponse)
async def chat(
    request: ChatTurnRequest, _: None = Depends(check_auth)
) -> ChatTurnResponse:
    """Answer a notebook chat turn proxied from OpenNotebook."""
    require_llm()
    print("chat request:", request.dict())

    lc_messages = to_lc_messages(request)
    if not lc_messages or all(isinstance(m, SystemMessage) for m in lc_messages):
        raise HTTPException(status_code=400, detail="No user message provided")

    agent = get_chat_agent()
    last_user_message = next(
        (
            m.get("content", "")
            for m in reversed(request.messages)
            if m.get("role") in ("human", "user") and m.get("content")
        ),
        "",
    )
    with traced(
        "chat-response",
        session_id=request.thread_id or None,
        tags=["chat"],
        input={"message": last_user_message},
        metadata={"model": config.LLM_MODEL},
    ) as span:
        result = await agent.ainvoke(
            {"messages": lc_messages}, config={"callbacks": [new_handler()]}
        )
        last = result["messages"][-1]
        content = last.content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        span.update(output={"content": content})

    return ChatTurnResponse(content=content)


@app.post("/chat/stream")
async def chat_stream(request: ChatTurnRequest, _: None = Depends(check_auth)):
    """SSE variant of /chat: streams tokens and tool-call events.

    Events (one JSON object per `data:` line):
      token / tool_call / tool_result / final / error
    """
    require_llm()

    lc_messages = to_lc_messages(request)
    if not lc_messages or all(isinstance(m, SystemMessage) for m in lc_messages):
        raise HTTPException(status_code=400, detail="No user message provided")

    agent = get_chat_agent()
    last_user_message = next(
        (
            m.get("content", "")
            for m in reversed(request.messages)
            if m.get("role") in ("human", "user") and m.get("content")
        ),
        "",
    )
    span_cm = traced(
        "chat-response",
        session_id=request.thread_id or None,
        tags=["chat", "stream"],
        input={"message": last_user_message},
        metadata={"model": config.LLM_MODEL},
    )

    async def generator():
        with span_cm as span:
            async for line in stream_chat_turn(
                agent, lc_messages, callbacks=[new_handler()]
            ):
                if '"type": "final"' in line:
                    # tag the trace output before closing the span
                    try:
                        import json as _json

                        payload = _json.loads(line[6:].strip())
                        span.update(output={"content": payload.get("content")})
                    except (ValueError, KeyError):  # best-effort tracing only
                        logger.debug("could not tag trace output from final event")
                yield line

    return StreamingResponse(
        generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


class ExtractRequest(BaseModel):
    source_id: str = Field(..., description="OpenNotebook source id")
    insight_type: str = Field(
        default="核心观点", description="insight_type label stored in OpenNotebook"
    )


class ExtractResponse(BaseModel):
    job_id: str
    status: str


@app.post("/extract", response_model=ExtractResponse, status_code=202)
async def extract(
    request: ExtractRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(check_auth),
) -> ExtractResponse:
    """Run the viewpoint extraction pipeline on a source (async job)."""
    require_llm()
    job = create_job(request.source_id, request.insight_type)
    background_tasks.add_task(run_extraction_job, job)
    return ExtractResponse(job_id=job.id, status=job.status)


@app.get("/jobs/{job_id}")
async def job_status(job_id: str, _: None = Depends(check_auth)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    langfuse_base = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")
    return {
        "job_id": job.id,
        "source_id": job.source_id,
        "source_title": job.source_title,
        "insight_type": job.insight_type,
        "status": job.status,
        "error": job.error,
        "sections": job.sections,
        # Langfuse session id == job id; trace link for quick access.
        "trace_id": job.trace_id,
        "trace_url": (
            f"{langfuse_base}/trace/{job.trace_id}" if job.trace_id else None
        ),
        "log": job.log,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
