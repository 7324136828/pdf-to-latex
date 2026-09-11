import io
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

TESTS_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = TESTS_ROOT.parent
PROJECT_ROOT = BACKEND_ROOT.parent

for p in (PROJECT_ROOT, BACKEND_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fastapi.testclient import TestClient

try:
    from backend.main import app
    from backend.services.job_manager import JobManager
    from backend.api.schemas import ConversionOptions
except ImportError:
    from main import app
    from services.job_manager import JobManager
    from api.schemas import ConversionOptions


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.job_manager = JobManager(base_temp_dir=Path(self.temp_dir.name))
        self.client = TestClient(app)

    def test_health_check(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("gpu_available", data)
        self.assertIn("has_rtx", data)

    def test_temp_directory_creation_and_job_lifecycle(self):
        options = ConversionOptions(device="cpu", mode="traditional_only")
        job = self.job_manager.create_job(options)

        # Check that directories were created in system temp path
        self.assertTrue(job.temp_dir.exists())
        self.assertTrue(job.input_dir.exists())
        self.assertTrue(job.output_dir.exists())
        self.assertTrue(job.work_dir.exists())

        # Test saving uploaded content
        file_path = self.job_manager.save_upload_file(job, "test.pdf", b"%PDF-1.4 test")
        self.assertTrue(file_path.exists())
        self.assertEqual(file_path.read_bytes(), b"%PDF-1.4 test")
        self.assertEqual(job.files_count, 1)

        # Write dummy output file and catalog
        output_file = job.output_dir / "chapter1.tex"
        output_file.write_text(r"\documentclass{article}\begin{document}Hello\end{document}", encoding="utf-8")

        content = self.job_manager.get_output_file_content(job, "chapter1.tex")
        self.assertIn("Hello", content)

        # Security check: Path traversal prevention
        with self.assertRaises(PermissionError):
            self.job_manager.get_output_file_content(job, "../../secrets.txt")

        # Test zip creation
        zip_bytes = self.job_manager.create_zip_archive(job)
        self.assertTrue(len(zip_bytes) > 0)

        # Test deletion
        self.job_manager.delete_job(job.job_id)
        self.assertFalse(job.temp_dir.exists())

    def test_jobs_survive_restart_and_in_flight_work_becomes_resumable(self):
        options = ConversionOptions(device="cpu", mode="traditional_only")
        job = self.job_manager.create_job(options)
        first = self.job_manager.save_upload_file(job, "book.pdf", b"%PDF first")
        second = self.job_manager.save_upload_file(job, "book.pdf", b"%PDF second")
        self.assertEqual(first.name, "book.pdf")
        self.assertEqual(second.name, "book_2.pdf")
        self.assertEqual(job.source_files, ["book.pdf", "book_2.pdf"])

        with job.lock:
            job.status = "converting"
            job.message = "Working"
        self.job_manager._persist_job(job)

        restored_manager = JobManager(base_temp_dir=Path(self.temp_dir.name))
        restored = restored_manager.get_job(job.job_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.status, "interrupted")
        self.assertEqual(restored.files_count, 2)
        self.assertEqual(restored.source_files, ["book.pdf", "book_2.pdf"])
        self.assertEqual(restored.options.mode, "traditional_only")
        self.assertIn("Resume", restored.message)

        with patch.object(restored_manager, "run_job_async") as run_async:
            resumed = restored_manager.resume_job(job.job_id)
        self.assertEqual(resumed.status, "queued")
        self.assertIsNone(resumed.error)
        run_async.assert_called_once_with(job.job_id)

    def test_discard_hides_an_active_job_until_worker_cleanup(self):
        job = self.job_manager.create_job(
            ConversionOptions(device="cpu", mode="traditional_only")
        )
        with job.lock:
            job.status = "converting"

        self.assertTrue(self.job_manager.delete_job(job.job_id))
        self.assertTrue(job.discard_requested.is_set())
        self.assertEqual(job.status, "discarding")
        self.assertEqual(self.job_manager.list_jobs(), [])

        self.job_manager._finalize_discard(job)
        self.assertIsNone(self.job_manager.get_job(job.job_id))
        self.assertFalse(job.temp_dir.exists())

    def test_recovery_endpoints_list_and_resume_an_interrupted_job(self):
        job = self.job_manager.create_job(
            ConversionOptions(device="cpu", mode="traditional_only")
        )
        self.job_manager.save_upload_file(job, "history.pdf", b"%PDF history")
        with job.lock:
            job.status = "interrupted"
            job.message = "Ready to resume"
        self.job_manager._persist_job(job)

        with patch("backend.api.routes.job_manager", self.job_manager):
            response = self.client.get("/api/jobs")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()[0]["job_id"], job.job_id)
            self.assertEqual(response.json()[0]["status"], "interrupted")
            self.assertEqual(response.json()[0]["source_files"], ["history.pdf"])

            with patch.object(self.job_manager, "run_job_async") as run_async:
                response = self.client.post(f"/api/jobs/{job.job_id}/resume")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "queued")
        run_async.assert_called_once_with(job.job_id)

    def test_convert_endpoint_validation(self):
        # Empty submission
        response = self.client.post("/api/convert", files={})
        self.assertEqual(response.status_code, 422)

        # Non-pdf submission
        files = [("files", ("test.txt", b"Hello world", "text/plain"))]
        response = self.client.post("/api/convert", files=files)
        self.assertEqual(response.status_code, 400)
