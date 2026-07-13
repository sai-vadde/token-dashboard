"""Source-pipeline contract and registry for transcript ingestion."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class PipelineOutput:
    """Canonical rows plus provider-owned updates emitted by one record."""

    message: Optional[dict] = None
    tools: List[dict] = field(default_factory=list)
    updates: List[dict] = field(default_factory=list)


class SourcePipeline(Protocol):
    source: str
    replay_changed_files: bool

    def new_context(self) -> Dict[str, Any]: ...
    def accepts(self, record: dict) -> bool: ...
    def parse(self, record: dict, fallback_slug: str, context: dict, offset: int) -> PipelineOutput: ...
    def persist_updates(self, conn, updates: List[dict]) -> None: ...
    def after_scan(self, conn, root) -> None: ...


_PIPELINES: Dict[str, SourcePipeline] = {}


def register_pipeline(pipeline: SourcePipeline) -> None:
    if not pipeline.source:
        raise ValueError("pipeline source must not be empty")
    _PIPELINES[pipeline.source] = pipeline


def get_pipeline(source: str) -> SourcePipeline:
    try:
        return _PIPELINES[source]
    except KeyError as exc:
        supported = ", ".join(sorted(_PIPELINES)) or "none"
        raise ValueError(f"unsupported source {source!r}; registered: {supported}") from exc


def registered_sources() -> List[str]:
    return sorted(_PIPELINES)
