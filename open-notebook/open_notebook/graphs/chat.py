import asyncio
import sqlite3
from typing import Annotated, Optional

import httpx
from ai_prompter import Prompter
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
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


def _invoke_agent_service(system_prompt: str, messages: list, thread_id: str) -> str:
    """Proxy the chat turn to the external agent service.

    The service receives the notebook-context system prompt plus the full
    conversation history (ON's LangGraph checkpoint remains the source of
    truth), and returns the agent's final answer.
    """
    headers = {}
    if AGENT_SERVICE_TOKEN:
        headers["Authorization"] = f"Bearer {AGENT_SERVICE_TOKEN}"
    payload = {
        "thread_id": thread_id,
        "system_prompt": system_prompt,
        "messages": [
            {"role": m.type, "content": extract_text_content(m.content)}
            for m in messages
        ],
    }
    try:
        response = httpx.post(
            f"{AGENT_SERVICE_URL}/chat",
            json=payload,
            headers=headers,
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
            content = _invoke_agent_service(
                system_prompt, state.get("messages", []), thread_id
            )
            return {"messages": AIMessage(content=clean_thinking_content(content))}

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
