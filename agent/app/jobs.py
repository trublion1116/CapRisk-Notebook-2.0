import time
import uuid
from dataclasses import dataclass, field


@dataclass
class ExtractionJob:
    id: str
    source_id: str
    insight_type: str
    status: str = "running"  # running | done | failed
    error: str | None = None
    source_title: str | None = None
    sections: int = 0
    log: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def record(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"[{stamp}] {message}")


_jobs: dict[str, ExtractionJob] = {}


def create_job(source_id: str, insight_type: str) -> ExtractionJob:
    job = ExtractionJob(
        id=uuid.uuid4().hex[:12],
        source_id=source_id,
        insight_type=insight_type,
    )
    _jobs[job.id] = job
    return job


def get_job(job_id: str) -> ExtractionJob | None:
    return _jobs.get(job_id)
