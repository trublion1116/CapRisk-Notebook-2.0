import asyncio
import json
import sqlite3
from typing import Annotated, Optional

import httpx
from ai_prompter import Prompter
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.config import (
    AGENT_SERVICE_TIMEOUT,
    AGENT_SERVICE_TOKEN,
    AGENT_SERVICE_URL,
    LANGGRAPH_CHECKPOINT_FILE,
)
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import ExternalServiceError, OpenNotebookError
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content


class ThreadState(TypedDict):
    messages: Annotated[list, add_messages]
    notebook: Optional[Notebook]
    context: Optional[str]
    context_config: Optional[dict]
    model_override: Optional[str]


def _agent_service_headers() -> dict:
    headers = {}
    if AGENT_SERVICE_TOKEN:
        headers["Authorization"] = f"Bearer {AGENT_SERVICE_TOKEN}"
    return headers


def _agent_service_payload(system_prompt: str, messages: list, thread_id: str) -> dict:
    return {
        "thread_id": thread_id,
        "system_prompt": system_prompt,
        "messages": [
            {"role": m.type, "content": extract_text_content(m.content)}
            for m in messages
        ],
    }


def _merge_tool_event(collected: list, event: dict) -> None:
    """Fold a streamed tool_call/tool_result event into a toolCalls list.

    tool_call creates the entry (status running); tool_result updates the
    matching entry (matched by id, falling back to name) with the final
    status and truncated result. The list is persisted on the AI message so
    session history replays show the tool tray after refresh.
    """
    if event.get("type") == "tool_call":
        collected.append(
            {
                "id": event.get("id") or "",
                "name": event.get("name") or "",
                "args": event.get("args") or {},
                "status": "running",
            }
        )
    elif event.get("type") == "tool_result":
        for tc in reversed(collected):
            if tc["id"] == event.get("id") or (
                not tc["id"] and tc["name"] == event.get("name")
            ):
                tc["status"] = event.get("status") or "success"
                tc["result"] = event.get("content") or ""
                break


def _stream_agent_service(system_prompt: str, messages: list, thread_id: str) -> tuple:
    """Proxy the chat turn to the external agent service's SSE endpoint.

    Intermediate events (tokens, tool calls) are forwarded through the
    LangGraph stream writer (custom stream mode) so streaming consumers can
    render them; the final answer text and the merged tool-call list are
    returned for the node state.

    Under a plain (non-streaming) invoke the writer is a no-op, so this path
    behaves like the old synchronous proxy.
    """
    try:
        writer = get_stream_writer()
    except RuntimeError:  # no stream context (plain invoke) - events dropped
        writer = lambda event: None  # noqa: E731

    tool_calls: list = []
    try:
        with httpx.Client(timeout=AGENT_SERVICE_TIMEOUT) as client:
            with client.stream(
                "POST",
                f"{AGENT_SERVICE_URL}/chat/stream",
                json=_agent_service_payload(system_prompt, messages, thread_id),
                headers=_agent_service_headers(),
            ) as response:
                response.raise_for_status()
                final_content = ""
                error_event = None
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[len("data: ") :])
                    except json.JSONDecodeError:
                        continue
                    event_type = event.get("type")
                    if event_type == "final":
                        final_content = event.get("content") or ""
                    elif event_type == "error":
                        error_event = event.get("message") or "agent error"
                    else:
                        if event_type in ("tool_call", "tool_result"):
                            _merge_tool_event(tool_calls, event)
                        writer(event)
    except httpx.HTTPError as e:
        raise ExternalServiceError(f"Agent service request failed: {e}") from e

    if error_event is not None:
        raise ExternalServiceError(f"Agent service failed: {error_event}")
    if not final_content.strip():
        raise ExternalServiceError("Agent service returned an empty response")
    return final_content, tool_calls


def _invoke_agent_service(system_prompt: str, messages: list, thread_id: str) -> str:
    """Proxy the chat turn to the external agent service.

    The service receives the notebook-context system prompt plus the full
    conversation history (ON's LangGraph checkpoint remains the source of
    truth), and returns the agent's final answer.
    """
    try:
        response = httpx.post(
            f"{AGENT_SERVICE_URL}/chat",
            json=_agent_service_payload(system_prompt, messages, thread_id),
            headers=_agent_service_headers(),
            timeout=AGENT_SERVICE_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as e:
        raise ExternalServiceError(f"Agent service request failed: {e}") from e
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ExternalServiceError("Agent service returned an empty response")
    return content


def call_model_with_messages(state: ThreadState, config: RunnableConfig) -> dict:
    try:
        system_prompt = Prompter(prompt_template="chat/system").render(data=state)  # type: ignore[arg-type]

        if AGENT_SERVICE_URL:
            thread_id = str(config.get("configurable", {}).get("thread_id", ""))
            content, tool_calls = _stream_agent_service(
                system_prompt, state.get("messages", []), thread_id
            )
            return {
                "messages": AIMessage(
                    content=clean_thinking_content(content),
                    additional_kwargs={"tool_calls": tool_calls},
                )
            }

        payload = [SystemMessage(content=system_prompt)] + state.get("messages", [])
        model_id = config.get("configurable", {}).get("model_id") or state.get(
            "model_override"
        )

        # Handle async model provisioning from sync context
        def run_in_new_loop():
            """Run the async function in a new event loop"""
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                return new_loop.run_until_complete(
                    provision_langchain_model(
                        str(payload), model_id, "chat", max_tokens=8192
                    )
                )
            finally:
                new_loop.close()
                asyncio.set_event_loop(None)

        try:
            # Try to get the current event loop
            asyncio.get_running_loop()
            # If we're in an event loop, run in a thread with a new loop
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_new_loop)
                model = future.result()
        except RuntimeError:
            # No event loop running, safe to use asyncio.run()
            model = asyncio.run(
                provision_langchain_model(
                    str(payload),
                    model_id,
                    "chat",
                    max_tokens=8192,
                )
            )

        ai_message = model.invoke(payload)

        # Clean thinking content from AI response (e.g., <think>...</think> tags)
        content = extract_text_content(ai_message.content)
        cleaned_content = clean_thinking_content(content)
        cleaned_message = ai_message.model_copy(update={"content": cleaned_content})

        return {"messages": cleaned_message}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


conn = sqlite3.connect(
    LANGGRAPH_CHECKPOINT_FILE,
    check_same_thread=False,
)
memory = SqliteSaver(conn)

agent_state = StateGraph(ThreadState)
agent_state.add_node("agent", call_model_with_messages)
agent_state.add_edge(START, "agent")
agent_state.add_edge("agent", END)
graph = agent_state.compile(checkpointer=memory)
