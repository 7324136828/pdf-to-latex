from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OutputFile(BaseModel):
    filename: str
    chapter_title: str
    size_bytes: int
    sha256: str
    converter: str = "traditional"


class ConversionOptions(BaseModel):
    device: Literal["auto", "cuda", "cpu"] = "auto"
    mode: Literal["hybrid", "traditional_only", "pdf2tex_only"] = "hybrid"
    dpi: int = Field(default=300, ge=72, le=600)
    min_section_chars: int = Field(default=3000, ge=0)
    force: bool = False


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "converting", "interrupted", "discarding", "completed", "failed"]
    progress: float
    message: str
    files_count: int = 0
    source_files: list[str] = Field(default_factory=list)
    chapters_count: int = 0
    logs: list[str] = Field(default_factory=list)
    outputs: list[OutputFile] = Field(default_factory=list)
    created_at: str
    completed_at: str | None = None
    error: str | None = None


class JobSummaryResponse(BaseModel):
    job_id: str
    status: Literal["queued", "converting", "interrupted", "discarding", "completed", "failed"]
    message: str
    files_count: int = 0
    source_files: list[str] = Field(default_factory=list)
    chapters_count: int = 0
    created_at: str
    completed_at: str | None = None


class HealthResponse(BaseModel):
    status: str
    gpu_available: bool
    gpu_names: list[str]
    has_rtx: bool
    python_version: str
