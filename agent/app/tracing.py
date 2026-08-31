"""Langfuse tracing helpers.

Follows the Langfuse interoperability pattern: each request/job is wrapped in
a root span (which becomes the trace), trace attributes are set via
propagate_attributes(), and the LangChain CallbackHandler inherits the active
context so the whole LangGraph execution nests under the trace.

The Langfuse client is configured purely via environment variables
(LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL); when they
are absent the SDK degrades to a no-op with a warning, so tracing never
breaks the service.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler


def new_handler() -> CallbackHandler:
    """Create a CallbackHandler that nests under the active Langfuse span."""
    return CallbackHandler()


@contextmanager
def traced(
    name: str,
    *,
    session_id: str | None = None,
    tags: list[str] | None = None,
    input: dict | None = None,
    metadata: dict | None = None,
) -> Iterator[Any]:
    """Open a root span (trace) with trace-level attributes.

    Usage:
        with traced("chat-response", session_id=sid, tags=["chat"],
                    input={"message": msg}) as span:
            result = ...  # run LLM work with new_handler() in callbacks
            span.update(output={"content": answer})
    """
    langfuse = get_client()
    with langfuse.start_as_current_observation(as_type="span", name=name) as span:
        updates: dict = {}
        if input is not None:
            updates["input"] = input
        if metadata is not None:
            updates["metadata"] = metadata
        if updates:
            span.update(**updates)
        with propagate_attributes(
            trace_name=name,
            session_id=session_id,
            tags=tags or [],
        ):
            yield span


@contextmanager
def child_span(name: str, input: dict | None = None) -> Iterator[Any]:
    """Open a child span under the active trace (e.g. non-LLM steps)."""
    langfuse = get_client()
    with langfuse.start_as_current_observation(as_type="span", name=name) as span:
        if input is not None:
            span.update(input=input)
        yield span
