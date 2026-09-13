from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import PlainTextResponse

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent

for p in (PROJECT_ROOT, BACKEND_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from backend.api.schemas import (
        ConversionOptions,
        HealthResponse,
        JobSummaryResponse,
        JobStatusResponse,
    )
    from backend.pdfconv.device import detect_nvidia_gpus
    from backend.services.job_manager import job_manager
except ImportError:
    from api.schemas import (
        ConversionOptions,
        HealthResponse,
        JobSummaryResponse,
        JobStatusResponse,
    )
    from pdfconv.device import detect_nvidia_gpus
    from services.job_manager import job_manager

router = APIRouter(prefix="/api")


def _job_response(job) -> JobStatusResponse:
    with job.lock:
        return JobStatusResponse(
            job_id=job.job_id,
            status=job.status,
            progress=job.progress,
            message=job.message,
            files_count=job.files_count,
            source_files=list(job.source_files),
            chapters_count=job.chapters_count,
            logs=list(job.logs),
            outputs=list(job.outputs),
            created_at=job.created_at,
            completed_at=job.completed_at,
            error=job.error,
        )


def _job_summary(job) -> JobSummaryResponse:
    with job.lock:
        return JobSummaryResponse(
            job_id=job.job_id,
            status=job.status,
            message=job.message,
            files_count=job.files_count,
            source_files=list(job.source_files),
            chapters_count=job.chapters_count,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )


@router.get("/health", response_model=HealthResponse)
def health_check():
    gpu = detect_nvidia_gpus()
    return HealthResponse(
        status="ok",
        gpu_available=gpu.available,
        gpu_names=gpu.names,
        has_rtx=gpu.has_rtx,
        python_version=sys.version.split()[0],
    )


@router.post("/convert")
async def create_conversion_job(
    files: list[UploadFile] = File(...),
    device: str = Form("auto"),
    mode: str = Form("hybrid"),
    dpi: int = Form(300),
    min_section_chars: int = Form(3000),
    force: bool = Form(False),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    valid_files = [f for f in files if f.filename and f.filename.lower().endswith(".pdf")]
    if not valid_files:
        raise HTTPException(
            status_code=400, detail="No valid PDF files found in submission."
        )

    options = ConversionOptions(
        device=device,
        mode=mode,
        dpi=dpi,
        min_section_chars=min_section_chars,
        force=force,
    )

    job = job_manager.create_job(options)

    for upload_file in valid_files:
        content = await upload_file.read()
        job_manager.save_upload_file(job, upload_file.filename, content)

    job_manager.run_job_async(job.job_id)

    return {
        "job_id": job.job_id,
        "status": job.status,
        "message": f"Queued {len(valid_files)} file(s) for conversion.",
        "files_count": len(valid_files),
    }


@router.get("/jobs", response_model=list[JobSummaryResponse])
def list_jobs():
    return [_job_summary(job) for job in job_manager.list_jobs()]


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    return _job_response(job)


@router.post("/jobs/{job_id}/resume", response_model=JobStatusResponse)
def resume_job(job_id: str):
    job = job_manager.resume_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_response(job)


@router.get("/jobs/{job_id}/files/{filename:path}")
def get_job_file(job_id: str, filename: str, download: bool = False):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    try:
        content = job_manager.get_output_file_content(job, filename)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied.")

    if content is None:
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found.")

    headers = {}
    if download:
        safe_filename = filename.replace("/", "_").replace("\\", "_")
        headers["Content-Disposition"] = f'attachment; filename="{safe_filename}"'

    return PlainTextResponse(content=content, headers=headers)


@router.get("/jobs/{job_id}/download-zip")
def download_job_zip(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job.status != "completed":
        raise HTTPException(
            status_code=400, detail="Job is not completed yet."
        )

    zip_bytes = job_manager.create_zip_archive(job)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="latex_output_{job_id[:8]}.zip"'
        },
    )


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    success = job_manager.delete_job(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"status": "deleted", "job_id": job_id}
