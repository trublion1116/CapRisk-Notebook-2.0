from fastapi import APIRouter, HTTPException
from loguru import logger

from api.models import (
    AgentInsightCreationResponse,
    CreateAgentInsightRequest,
    NoteResponse,
    SaveAsNoteRequest,
    SourceInsightResponse,
)
from open_notebook.domain.notebook import Source, SourceInsight
from open_notebook.exceptions import (
    InvalidInputError,
    NotFoundError,
    OpenNotebookError,
)

router = APIRouter()


@router.post("/insights", response_model=AgentInsightCreationResponse, status_code=202)
async def create_insight(request: CreateAgentInsightRequest):
    """Create an insight directly on a source (e.g. from an external agent service).

    Insight creation runs asynchronously in the background via the job queue
    (with automatic embedding). Poll GET /sources/{source_id}/insights to see
    when the insight is ready.
    """
    try:
        source = await Source.get(request.source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        command_id = await source.add_insight(request.insight_type, request.content)

        return AgentInsightCreationResponse(
            source_id=request.source_id,
            insight_type=request.insight_type,
            command_id=command_id,
        )
    except HTTPException:
        raise
    except InvalidInputError:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error creating insight for source {request.source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating insight")


@router.get("/insights/{insight_id}", response_model=SourceInsightResponse)
async def get_insight(insight_id: str):
    """Get a specific insight by ID."""
    try:
        insight = await SourceInsight.get(insight_id)
        if not insight:
            raise HTTPException(status_code=404, detail="Insight not found")

        # Get source ID from the insight relationship
        source = await insight.get_source()

        return SourceInsightResponse(
            id=insight.id or "",
            source_id=source.id or "",
            insight_type=insight.insight_type,
            content=insight.content,
            created=insight.created.isoformat() if insight.created else None,
            updated=insight.updated.isoformat() if insight.updated else None,
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching insight {insight_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching insight")


@router.delete("/insights/{insight_id}")
async def delete_insight(insight_id: str):
    """Delete a specific insight."""
    try:
        insight = await SourceInsight.get(insight_id)
        if not insight:
            raise HTTPException(status_code=404, detail="Insight not found")

        await insight.delete()

        return {"message": "Insight deleted successfully"}
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting insight {insight_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error deleting insight")


@router.post("/insights/{insight_id}/save-as-note", response_model=NoteResponse)
async def save_insight_as_note(insight_id: str, request: SaveAsNoteRequest):
    """Convert an insight to a note."""
    try:
        insight = await SourceInsight.get(insight_id)
        if not insight:
            raise HTTPException(status_code=404, detail="Insight not found")

        # Use the existing save_as_note method from the domain model
        note = await insight.save_as_note(request.notebook_id)

        return NoteResponse(
            id=note.id or "",
            title=note.title,
            content=note.content,
            note_type=note.note_type,
            created=str(note.created),
            updated=str(note.updated),
        )
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error saving insight {insight_id} as note: {str(e)}")
        raise HTTPException(status_code=500, detail="Error saving insight as note")
