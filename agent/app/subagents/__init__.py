"""Capability subagent registry.

All subagents available to the orchestrator agent. Adding a new capability:
1. Create a module with a ``build(...) -> Dict[str, Any]`` factory returning a
   deepagents SubAgent spec (name/description/system_prompt/tools).
2. Import and append it in :func:`registered_subagents` below.

Every /extract run registers ALL subagents; the orchestrator decides dispatch
order via its planning.
"""

from typing import Any

from app.on_client import OpenNotebookClient
from app.subagents import viewpoint_extraction


def registered_subagents(
    client: OpenNotebookClient,
    sections: list[dict[str, Any]],
    source_id: str,
    insight_type: str,
) -> list[dict[str, Any]]:
    """Build every registered subagent with tools bound to this job."""
    return [
        viewpoint_extraction.build(client, sections),
    ]
