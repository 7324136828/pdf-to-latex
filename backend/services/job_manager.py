from __future__ import annotations

import csv
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent

for p in (PROJECT_ROOT, BACKEND_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from backend.api.schemas import ConversionOptions, OutputFile
    from backend.pdfconv.fsutil import file_digest
    from backend.pdfconv.orchestrator import Converter, Options
except ImportError:
    from api.schemas import ConversionOptions, OutputFile
    from pdfconv.fsutil import file_digest
    from pdfconv.orchestrator import Converter, Options


@dataclass
class Job:
    job_id: str
    temp_dir: Path
    input_dir: Path
    output_dir: Path
    work_dir: Path
    options: ConversionOptions
    status: str = "queued"  # queued, converting, interrupted, discarding, completed, failed
    progress: float = 0.0
    message: str = "Job created"
    files_count: int = 0
    source_files: List[str] = field(default_factory=list)
    chapters_count: int = 0
    logs: List[str] = field(default_factory=list)
    outputs: List[OutputFile] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    error: Optional[str] = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    discard_requested: threading.Event = field(default_factory=threading.Event)
    worker_scheduled: threading.Event = field(default_factory=threading.Event)

    def add_log(self, text: str) -> None:
        with self.lock:
            for line in text.splitlines():
                clean = line.strip()
                if clean:
                    self.logs.append(clean)


class JobManager:
    """Manages temporary execution pipelines, background jobs, and file retrieval."""

    def __init__(self, base_temp_dir: Optional[Path] = None):
        self.base_temp_dir = (
            base_temp_dir
            if base_temp_dir
            else Path(tempfile.gettempdir()) / "pdf_to_latex_jobs"
        )
        self.base_temp_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: Dict[str, Job] = {}
        self.lock = threading.Lock()
        self._persist_lock = threading.Lock()
        self._load_jobs()

    @staticmethod
    def _manifest_path(job_dir: Path) -> Path:
        return job_dir / "job.json"

    def _persist_job(self, job: Job) -> None:
        """Atomically persist enough state to recover a job after a restart."""
        with job.lock:
            payload = {
                "version": 1,
                "job_id": job.job_id,
                "options": job.options.model_dump(mode="json"),
                "status": job.status,
                "progress": job.progress,
                "message": job.message,
                "files_count": job.files_count,
                "source_files": list(job.source_files),
                "chapters_count": job.chapters_count,
                "logs": list(job.logs),
                "outputs": [output.model_dump(mode="json") for output in job.outputs],
                "created_at": job.created_at,
                "completed_at": job.completed_at,
                "error": job.error,
            }

        manifest = self._manifest_path(job.temp_dir)
        pending = manifest.with_suffix(".json.tmp")
        with self._persist_lock:
            try:
                if not job.temp_dir.exists():
                    return
                pending.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(pending, manifest)
            except FileNotFoundError:
                # A confirmed discard may remove the directory while a worker
                # is flushing its final status.
                return

    def _load_jobs(self) -> None:
        """Restore jobs saved by an earlier backend process."""
        recovered: List[Job] = []
        for job_dir in self.base_temp_dir.iterdir():
            manifest = self._manifest_path(job_dir)
            if not job_dir.is_dir() or not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("status") == "discarding":
                    shutil.rmtree(job_dir, ignore_errors=True)
                    continue
                job_id = str(data["job_id"])
                if job_id != job_dir.name:
                    continue
                options = ConversionOptions.model_validate(data["options"])
                status = str(data.get("status", "interrupted"))
                was_interrupted = status in {"queued", "converting"}
                if was_interrupted:
                    status = "interrupted"

                input_dir = job_dir / "input"
                output_dir = job_dir / "output"
                work_dir = job_dir / "work_dir"
                for directory in (input_dir, output_dir, work_dir):
                    directory.mkdir(parents=True, exist_ok=True)

                outputs = [
                    OutputFile.model_validate(output)
                    for output in data.get("outputs", [])
                ]
                job = Job(
                    job_id=job_id,
                    temp_dir=job_dir,
                    input_dir=input_dir,
                    output_dir=output_dir,
                    work_dir=work_dir,
                    options=options,
                    status=status,
                    progress=float(data.get("progress", 0.0)),
                    message=(
                        "Conversion was interrupted. Resume to continue from the saved checkpoint."
                        if was_interrupted
                        else str(data.get("message", "Recovered job"))
                    ),
                    files_count=max(
                        int(data.get("files_count", 0)),
                        sum(1 for path in input_dir.iterdir() if path.is_file()),
                    ),
                    source_files=(
                        [str(name) for name in data.get("source_files", [])]
                        or sorted(path.name for path in input_dir.iterdir() if path.is_file())
                    ),
                    chapters_count=int(data.get("chapters_count", len(outputs))),
                    logs=[str(line) for line in data.get("logs", [])],
                    outputs=outputs,
                    created_at=str(data["created_at"]),
                    completed_at=data.get("completed_at"),
                    error=data.get("error"),
                )
                if was_interrupted:
                    job.logs.append("Backend restarted before this conversion finished.")
                recovered.append(job)
            except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
                # A partial/corrupt manifest must not prevent the API from starting.
                continue

        with self.lock:
            for job in recovered:
                self.jobs[job.job_id] = job
        for job in recovered:
            if job.status == "interrupted":
                self._persist_job(job)

    def create_job(self, options: ConversionOptions) -> Job:
        job_id = str(uuid.uuid4())
        job_temp_dir = self.base_temp_dir / job_id
        input_dir = job_temp_dir / "input"
        output_dir = job_temp_dir / "output"
        work_dir = job_temp_dir / "work_dir"

        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)

        job = Job(
            job_id=job_id,
            temp_dir=job_temp_dir,
            input_dir=input_dir,
            output_dir=output_dir,
            work_dir=work_dir,
            options=options,
        )

        with self.lock:
            self.jobs[job_id] = job

        self._persist_job(job)

        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        with self.lock:
            return self.jobs.get(job_id)

    def list_jobs(self) -> List[Job]:
        with self.lock:
            jobs = [job for job in self.jobs.values() if job.status != "discarding"]
        return sorted(jobs, key=lambda job: job.created_at, reverse=True)

    def save_upload_file(self, job: Job, filename: str, content: bytes) -> Path:
        safe_name = Path(filename).name
        dest = job.input_dir / safe_name
        suffix = dest.suffix
        stem = dest.stem
        counter = 2
        while dest.exists():
            dest = job.input_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        dest.write_bytes(content)
        with job.lock:
            job.files_count += 1
            job.source_files.append(dest.name)
        self._persist_job(job)
        return dest

    def run_job_async(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        job.worker_scheduled.set()
        thread = threading.Thread(
            target=self._execute_job, args=(job_id,), daemon=True
        )
        thread.start()

    def resume_job(self, job_id: str) -> Optional[Job]:
        job = self.get_job(job_id)
        if not job:
            return None
        with job.lock:
            if job.status not in {"interrupted", "failed"}:
                return job
            job.status = "queued"
            job.progress = 0.0
            job.message = "Queued to resume from the saved checkpoint..."
            job.completed_at = None
            job.error = None
        job.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] Resume requested.")
        self._persist_job(job)
        self.run_job_async(job_id)
        return job

    def _execute_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return

        with job.lock:
            should_discard = job.discard_requested.is_set()
            if not should_discard:
                job.status = "converting"
                job.progress = 0.1
                job.message = "Starting conversion pipeline..."
        if should_discard:
            self._finalize_discard(job)
            return
        job.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] Job {job_id} started.")
        job.add_log(f"Working directory: {job.temp_dir}")
        job.add_log(f"Received {job.files_count} PDF file(s).")
        self._persist_job(job)

        try:
            # Configure orchestrator options
            conv_options = Options(
                source=job.input_dir,
                destination=job.output_dir,
                work_dir=job.work_dir,
                device=job.options.device,
                dpi=job.options.dpi,
                min_section_chars=job.options.min_section_chars,
                force=job.options.force,
                traditional_only=(job.options.mode == "traditional_only"),
                pdf2tex_only=(job.options.mode == "pdf2tex_only"),
                verbose=True,
                cancel_requested=job.discard_requested.is_set,
            )

            # Capture stdout/stderr from Converter
            class LogWriter:
                def __init__(self, target_job: Job):
                    self.target_job = target_job
                    self._buffer = ""

                def write(self, text: str) -> int:
                    if not text:
                        return 0
                    self._buffer += text
                    while "\n" in self._buffer:
                        line, self._buffer = self._buffer.split("\n", 1)
                        if line.strip():
                            self.target_job.add_log(line)
                    return len(text)

                def flush(self) -> None:
                    if self._buffer.strip():
                        self.target_job.add_log(self._buffer)
                        self._buffer = ""

            log_writer = LogWriter(job)
            old_stdout, old_stderr = sys.stdout, sys.stderr

            try:
                sys.stdout = log_writer
                sys.stderr = log_writer
                converter = Converter(conv_options)
                summary = converter.run()
            finally:
                log_writer.flush()
                sys.stdout = old_stdout
                sys.stderr = old_stderr

            if job.discard_requested.is_set():
                self._finalize_discard(job)
                return

            # Catalog outputs
            outputs = self._collect_outputs(job.output_dir)

            with job.lock:
                should_discard = job.discard_requested.is_set()
                if not should_discard:
                    job.outputs = outputs
                    job.chapters_count = len(outputs)
                    job.progress = 1.0
                    job.status = "completed"
                    job.message = f"Converted successfully: {len(outputs)} chapter(s) generated."
                    job.completed_at = datetime.now(timezone.utc).isoformat()

            if should_discard:
                self._finalize_discard(job)
                return

            job.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] Conversion completed: {len(outputs)} files generated.")
            self._persist_job(job)

        except Exception as exc:
            with job.lock:
                should_discard = job.discard_requested.is_set()
                if not should_discard:
                    job.status = "failed"
                    job.error = str(exc)
                    job.message = f"Conversion failed: {exc}"
                    job.completed_at = datetime.now(timezone.utc).isoformat()
            if should_discard:
                self._finalize_discard(job)
                return
            job.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR: {exc}")
            self._persist_job(job)

    def _finalize_discard(self, job: Job) -> None:
        with self.lock:
            if self.jobs.get(job.job_id) is job:
                self.jobs.pop(job.job_id, None)
        if job.temp_dir.exists():
            shutil.rmtree(job.temp_dir, ignore_errors=True)

    def _collect_outputs(self, output_dir: Path) -> List[OutputFile]:
        outputs: List[OutputFile] = []
        if not output_dir.exists():
            return outputs

        catalog_path = output_dir / "sections_catalog.tsv"
        catalog_info: Dict[str, Dict[str, str]] = {}
        if catalog_path.is_file():
            try:
                with catalog_path.open("r", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f, delimiter="\t")
                    for row in reader:
                        filename = row.get("file", "").strip()
                        if filename:
                            catalog_info[filename] = row
            except Exception:
                pass

        for path in sorted(output_dir.rglob("*.tex")):
            if not path.is_file():
                continue
            rel_name = path.relative_to(output_dir).as_posix()
            info = catalog_info.get(rel_name, {})
            title = info.get("title") or path.stem
            converter = info.get("converter", "traditional")
            digest = file_digest(path)
            outputs.append(
                OutputFile(
                    filename=rel_name,
                    chapter_title=title,
                    size_bytes=path.stat().st_size,
                    sha256=digest,
                    converter=converter,
                )
            )

        return outputs

    def get_output_file_content(self, job: Job, filename: str) -> Optional[str]:
        target = (job.output_dir / filename).resolve()
        # Security: Prevent directory traversal
        if not target.is_relative_to(job.output_dir.resolve()):
            raise PermissionError("Access denied: invalid file path.")
        if not target.is_file():
            return None
        return target.read_text(encoding="utf-8", errors="replace")

    def create_zip_archive(self, job: Job) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in job.output_dir.rglob("*"):
                if item.is_file():
                    arcname = item.relative_to(job.output_dir).as_posix()
                    zf.write(item, arcname=arcname)
        return buf.getvalue()

    def delete_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False

        with job.lock:
            active = job.status == "converting" or (
                job.status == "queued" and job.worker_scheduled.is_set()
            )
            if active:
                job.discard_requested.set()
                job.status = "discarding"
                job.message = "Discarding conversion..."

        if active:
            self._persist_job(job)
        else:
            self._finalize_discard(job)
        return True

    def cleanup_old_jobs(self, max_age_seconds: int = 7200) -> int:
        now = time.time()
        deleted = 0
        with self.lock:
            job_ids = list(self.jobs.keys())

        for jid in job_ids:
            job = self.get_job(jid)
            if not job:
                continue
            try:
                created_ts = datetime.fromisoformat(job.created_at).timestamp()
                if now - created_ts > max_age_seconds:
                    self.delete_job(jid)
                    deleted += 1
            except Exception:
                pass
        return deleted


# Global instance for backend
job_manager = JobManager()
