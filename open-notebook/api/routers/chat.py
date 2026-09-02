import asyncio
import json
import traceback
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, Field

from api.routers._chat_shared import (
    ChatMessage,
    SuccessResponse,
    extract_chat_messages,
    get_session_or_404,
)
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import ChatSession, Notebook
from open_notebook.exceptions import (
    NotFoundError,
    OpenNotebookError,
)
from open_notebook.graphs.chat import graph as chat_graph
from open_notebook.utils import token_count
from open_notebook.utils.context_builder import build_notebook_context
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.graph_utils import get_session_message_count

router = APIRouter()


# Request/Response models
class CreateSessionRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID to create session for")
    title: Optional[str] = Field(None, description="Optional session title")
    model_override: Optional[str] = Field(
        None, description="Optional model override for this session"
    )


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = Field(None, description="New session title")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )


class ChatSessionResponse(BaseModel):
    id: str = Field(..., description="Session ID")
    title: str = Field(..., description="Session title")
    notebook_id: Optional[str] = Field(None, description="Notebook ID")
    created: str = Field(..., description="Creation timestamp")
    updated: str = Field(..., description="Last update timestamp")
    message_count: Optional[int] = Field(
        None, description="Number of messages in session"
    )
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )


class ChatSessionWithMessagesResponse(ChatSessionResponse):
    messages: List[ChatMessage] = Field(
        default_factory=list, description="Session messages"
    )


class ExecuteChatRequest(BaseModel):
    session_id: str = Field(..., description="Chat session ID")
    message: str = Field(..., description="User message content")
    context: Dict[str, Any] = Field(
        ..., description="Chat context with sources and notes"
    )
    model_override: Optional[str] = Field(
        None, description="Optional model override for this message"
    )


class ExecuteChatResponse(BaseModel):
    session_id: str = Field(..., description="Session ID")
    messages: List[ChatMessage] = Field(..., description="Updated message list")


class BuildContextRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID")
    context_config: Dict[str, Any] = Field(..., description="Context configuration")


class BuildContextResponse(BaseModel):
    context: Dict[str, Any] = Field(..., description="Built context data")
    token_count: int = Field(..., description="Estimated token count")
    char_count: int = Field(..., description="Character count")


@router.get("/chat/sessions", response_model=List[ChatSessionResponse])
async def get_sessions(notebook_id: str = Query(..., description="Notebook ID")):
    """Get all chat sessions for a notebook."""
    try:
        # Get notebook to verify it exists
        notebook = await Notebook.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Get sessions for this notebook
        sessions_list = await notebook.get_chat_sessions()

        results = []
        for session in sessions_list:
            session_id = str(session.id)

            # Get message count from LangGraph state
            msg_count = await get_session_message_count(chat_graph, session_id)

            results.append(
                ChatSessionResponse(
                    id=session.id or "",
                    title=session.title or "Untitled Session",
                    notebook_id=notebook_id,
                    created=str(session.created),
                    updated=str(session.updated),
                    message_count=msg_count,
                    model_override=getattr(session, "model_override", None),
                )
            )

        return results
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching chat sessions: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching chat sessions: {str(e)}"
        )


@router.post("/chat/sessions", response_model=ChatSessionResponse)
async def create_session(request: CreateSessionRequest):
    """Create a new chat session."""
    try:
        # Verify notebook exists
        notebook = await Notebook.get(request.notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Create new session
        session = ChatSession(
            title=request.title
            or f"Chat Session {asyncio.get_event_loop().time():.0f}",
            model_override=request.model_override,
        )
        await session.save()

        # Relate session to notebook
        await session.relate_to_notebook(request.notebook_id)

        return ChatSessionResponse(
            id=session.id or "",
            title=session.title or "",
            notebook_id=request.notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=0,
            model_override=session.model_override,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error creating chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error creating chat session: {str(e)}"
        )


@router.get(
    "/chat/sessions/{session_id}", response_model=ChatSessionWithMessagesResponse
)
async def get_session(session_id: str):
    """Get a specific session with its messages."""
    try:
        # Get session (normalizes the ID and 404s if missing)
        full_session_id, session = await get_session_or_404(session_id)

        # Get session state from LangGraph to retrieve messages
        # Use sync get_state() in a thread since SqliteSaver doesn't support async
        thread_state = await asyncio.to_thread(
            chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": full_session_id}),
        )

        # Extract messages from state
        messages: list[ChatMessage] = []
        if thread_state and thread_state.values and "messages" in thread_state.values:
            messages = extract_chat_messages(thread_state.values["messages"])

        # Find notebook_id (we need to query the relationship)
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )

        notebook_id = notebook_query[0]["out"] if notebook_query else None

        if not notebook_id:
            # This might be an old session created before API migration
            logger.warning(
                f"No notebook relationship found for session {session_id} - may be an orphaned session"
            )

        return ChatSessionWithMessagesResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            notebook_id=notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=len(messages),
            messages=messages,
            model_override=getattr(session, "model_override", None),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching session: {str(e)}")


@router.put("/chat/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_session(session_id: str, request: UpdateSessionRequest):
    """Update session title."""
    try:
        # Get session (normalizes the ID and 404s if missing)
        full_session_id, session = await get_session_or_404(session_id)

        update_data = request.model_dump(exclude_unset=True)

        if "title" in update_data:
            session.title = update_data["title"]

        if "model_override" in update_data:
            session.model_override = update_data["model_override"]

        await session.save()

        # Find notebook_id
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )
        notebook_id = notebook_query[0]["out"] if notebook_query else None

        # Get message count from LangGraph state
        msg_count = await get_session_message_count(chat_graph, full_session_id)

        return ChatSessionResponse(
            id=session.id or "",
            title=session.title or "",
            notebook_id=notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=msg_count,
            model_override=session.model_override,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating session: {str(e)}")


@router.delete("/chat/sessions/{session_id}", response_model=SuccessResponse)
async def delete_session(session_id: str):
    """Delete a chat session."""
    try:
        # Get session (normalizes the ID and 404s if missing)
        _full_session_id, session = await get_session_or_404(session_id)

        await session.delete()

        return SuccessResponse(success=True, message="Session deleted successfully")
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting session: {str(e)}")


async def _prepare_chat_execution(
    request: ExecuteChatRequest,
) -> tuple[dict, str, ChatSession, Optional[str]]:
    """Shared setup for /chat/execute and its streaming variant.

    Resolves the session and notebook, loads the checkpointed state and
    appends the user message. Returns (state_values, full_session_id,
    session, model_override).
    """
    # Verify session exists (normalizes the ID and 404s if missing)
    full_session_id, session = await get_session_or_404(request.session_id)

    # Fetch notebook linked to this session
    notebook_query = await repo_query(
        "SELECT out FROM refers_to WHERE in = $session_id",
        {"session_id": ensure_record_id(full_session_id)},
    )
    notebook = None
    if notebook_query:
        notebook = await Notebook.get(notebook_query[0]["out"])

    # Determine model override (per-request override takes precedence over session-level)
    model_override = (
        request.model_override
        if request.model_override is not None
        else getattr(session, "model_override", None)
    )

    # Get current state
    # Use sync get_state() in a thread since SqliteSaver doesn't support async
    current_state = await asyncio.to_thread(
        chat_graph.get_state,
        config=RunnableConfig(configurable={"thread_id": full_session_id}),
    )

    # Prepare state for execution
    state_values = current_state.values if current_state else {}
    state_values["messages"] = state_values.get("messages", [])
    state_values["context"] = request.context
    state_values["notebook"] = notebook
    state_values["model_override"] = model_override

    # Add user message to state
    state_values["messages"].append(HumanMessage(content=request.message))

    return state_values, full_session_id, session, model_override


@router.post("/chat/execute", response_model=ExecuteChatResponse)
async def execute_chat(request: ExecuteChatRequest):
    """Execute a chat request and get AI response."""
    try:
        state_values, full_session_id, session, model_override = (
            await _prepare_chat_execution(request)
        )

        # Execute chat graph in a thread so the synchronous LangGraph invoke
        # (SqliteSaver checkpoints are sync) doesn't block the event loop and
        # freeze the rest of the API while the LLM responds. Mirrors the
        # get_state() calls above.
        # The lambda pins down which `invoke` overload is used; asyncio.to_thread
        # can't resolve overloaded callables on its own. The ignore is a langgraph
        # typing limitation: it accepts a partial state dict at runtime, but the
        # signature requires the full state type.
        result = await asyncio.to_thread(
            lambda: chat_graph.invoke(
                input=state_values,  # type: ignore[arg-type]
                config=RunnableConfig(
                    configurable={
                        "thread_id": full_session_id,
                        "model_id": model_override,
                    }
                ),
            )
        )

        # Update session timestamp
        await session.save()

        # Convert messages to response format
        messages = extract_chat_messages(result.get("messages", []))

        return ExecuteChatResponse(session_id=request.session_id, messages=messages)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        # Log detailed error with context for debugging
        logger.error(
            f"Error executing chat: {str(e)}\n"
            f"  Session ID: {request.session_id}\n"
            f"  Model override: {request.model_override}\n"
            f"  Traceback:\n{traceback.format_exc()}"
        )
        raise HTTPException(status_code=500, detail=f"Error executing chat: {str(e)}")


async def _stream_chat_response(
    request: ExecuteChatRequest,
) -> AsyncGenerator[str, None]:
    """Run the chat graph and stream its output as Server-Sent Events.

    Fork: the graph node forwards the external agent service's SSE events
    (tokens, tool calls) through LangGraph's custom stream mode when
    OPEN_NOTEBOOK_AGENT_URL is configured; without an agent service this
    simply emits the final answer. Event shapes (one JSON object per line):

      {"type": "token", "content": "..."}
      {"type": "tool_call", "id", "name", "args"}
      {"type": "tool_result", "id", "name", "content", "status"}
      {"type": "final", "content": "..."}
      {"type": "complete"} | {"type": "error", "message": "..."}
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    # Sentinel pushed when the background graph run finishes
    done = object()

    # Assigned by _prepare_chat_execution before the graph run is scheduled
    state_values: dict = {}
    full_session_id = ""
    model_override: Optional[str] = None
    graph_task: Optional[asyncio.Task] = None

    def emit(event: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def run_graph() -> None:
        try:
            # stream(["custom", "values"]):
            #   custom = events written by the node (agent SSE passthrough)
            #   values = full state snapshots; the last one carries the final
            # messages for the answer (also covers the no-agent-service path)
            for mode, chunk in chat_graph.stream(
                input=state_values,  # type: ignore[arg-type]
                config=RunnableConfig(
                    configurable={
                        "thread_id": full_session_id,
                        "model_id": model_override,
                    }
                ),
                stream_mode=["custom", "values"],
            ):
                if mode == "custom" and chunk is not None:
                    emit(chunk)
                elif mode == "values":
                    emit({"type": "_state", "messages": chunk.get("messages", [])})
        except Exception as e:  # noqa: BLE001 - stream boundary
            emit({"type": "error", "message": classify_error(e)[1]})
        finally:
            emit(done)

    try:
        state_values, full_session_id, session, model_override = (
            await _prepare_chat_execution(request)
        )
    except Exception as e:  # noqa: BLE001 - surfaced to the client below
        _, user_message = classify_error(e)
        yield f"data: {json.dumps({'type': 'error', 'message': user_message})}\n\n"
        return

    # Keep a reference: an unreferenced task can be garbage-collected mid-run
    graph_task = asyncio.get_running_loop().create_task(asyncio.to_thread(run_graph))

    last_ai_content = ""
    try:
        while True:
            event = await queue.get()
            if event is done:
                break
            if isinstance(event, dict) and event.get("type") == "_state":
                messages = event.get("messages", [])
                if messages:
                    last_msg = messages[-1]
                    if getattr(last_msg, "type", "") == "ai":
                        content = getattr(last_msg, "content", "")
                        if isinstance(content, list):
                            content = "".join(
                                part.get("text", "")
                                if isinstance(part, dict)
                                else str(part)
                                for part in content
                            )
                        if content:
                            last_ai_content = content
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # The final answer is the source of truth (tokens may drop mid-stream)
        yield (
            f"data: {json.dumps({'type': 'final', 'content': last_ai_content}, ensure_ascii=False)}\n\n"
        )
        yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        # Surface any graph-run failure and release the task reference
        if graph_task is not None and graph_task.done():
            graph_task.result() if not graph_task.cancelled() else None

        # Update session timestamp (same as the sync endpoint)
        await session.save()
    except Exception as e:  # noqa: BLE001 - mid-stream failure
        _, user_message = classify_error(e)
        logger.error(f"Error in chat streaming: {user_message}")
        yield f"data: {json.dumps({'type': 'error', 'message': user_message})}\n\n"


@router.post("/chat/execute/stream")
async def execute_chat_stream(request: ExecuteChatRequest):
    """Execute a chat request and stream the response as SSE.

    Same contract as POST /chat/execute, plus intermediate events (tokens,
    tool calls) when an external agent service is configured.
    """
    return StreamingResponse(
        _stream_chat_response(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/context", response_model=BuildContextResponse)
async def build_context(request: BuildContextRequest):
    """Build context for a notebook based on context configuration."""
    try:
        # Verify notebook exists
        notebook = await Notebook.get(request.notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        context_data, total_content = await build_notebook_context(
            notebook, request.context_config
        )

        char_count = len(total_content)
        estimated_tokens = token_count(total_content) if total_content else 0

        return BuildContextResponse(
            context=context_data, token_count=estimated_tokens, char_count=char_count
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error building context: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error building context: {str(e)}")
