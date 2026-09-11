"""Resumable state: what survives an interruption, and what invalidates it."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdfconv.checkpoint import CheckpointStore, exclusive_lock, valid_file


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.identity = {"source": "in", "destination": "out"}

    def store(self):
        return CheckpointStore(self.root / "tmp", "checkpoint", self.identity)

    def test_a_stored_page_comes_back_on_the_next_run(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(0, "page one", "traditional")
        self.assertEqual(entry.cached_page(0), "page one")

        again = self.store().document("book.pdf", {"sha256": "a"})
        self.assertEqual(again.cached_page(0), "page one")

    def test_a_changed_fingerprint_discards_the_pages(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(0, "page one", "traditional")

        changed = self.store().document("book.pdf", {"sha256": "b"})
        self.assertIsNone(changed.cached_page(0))

    def test_force_discards_the_pages_even_when_nothing_changed(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(0, "page one", "traditional")

        forced = self.store().document("book.pdf", {"sha256": "a"}, force=True)
        self.assertIsNone(forced.cached_page(0))

    def test_an_edited_page_file_is_not_trusted(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(2, "page three", "traditional")
        entry.page_path(2).write_text("tampered", encoding="utf-8")
        self.assertIsNone(entry.cached_page(2))

    def test_a_missing_page_file_is_not_trusted(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(0, "page one", "traditional")
        entry.page_path(0).unlink()
        self.assertIsNone(entry.cached_page(0))

    def test_an_unreadable_page_file_is_not_trusted(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(0, "page one", "traditional")
        with patch("pdfconv.checkpoint.store.file_digest",
                   side_effect=PermissionError("temporarily unreadable")):
            self.assertFalse(valid_file(entry.page_path(0), "a"))
            self.assertIsNone(entry.cached_page(0))

    def test_an_invalid_utf8_page_is_not_trusted_even_if_its_hash_matches(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(0, "page one", "traditional")
        damaged = bytes([0xff])
        entry.page_path(0).write_bytes(damaged)
        entry.data["pages"]["0"]["sha256"] = hashlib.sha256(damaged).hexdigest()
        self.assertIsNone(entry.cached_page(0))

    def test_restarting_extraction_invalidates_publication_before_a_page_finishes(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(0, "page one", "traditional")
        entry.finish_extraction()
        entry.finish_publication([{"file": "chapter.tex", "sha256": "b"}],
                                 settings={"minimum": 100}, method="traditional")
        entry.begin_extraction()

        # Simulate a shutdown before the next store_page commits anything.
        recovered = self.store().document("book.pdf", {"sha256": "a"})
        self.assertFalse(recovered.data["extracted"])
        self.assertFalse(recovered.split_done)
        self.assertEqual(recovered.cached_page(0), "page one")
        self.assertEqual(recovered.outputs(), entry.outputs())

    def test_a_checkpoint_for_another_run_is_ignored(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(0, "page one", "traditional")

        other = CheckpointStore(self.root / "tmp", "checkpoint",
                                {"source": "elsewhere", "destination": "out"})
        self.assertEqual(other.documents(), {})

    def test_an_unreadable_checkpoint_starts_over_rather_than_raising(self):
        store = self.store()
        store.document("book.pdf", {"sha256": "a"})
        store.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store().documents(), {})

    def test_peek_reports_state_without_resetting_it(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(0, "page one", "traditional")

        store = self.store()
        data = store.peek("book.pdf")
        self.assertEqual(data["fingerprint"]["sha256"], "a")
        self.assertIsNone(store.peek("missing.pdf"))
        # peeking must not have discarded anything
        self.assertEqual(store.document("book.pdf", {"sha256": "a"}).cached_page(0),
                         "page one")

    def test_documents_are_kept_separately(self):
        store = self.store()
        store.document("one.pdf", {"sha256": "a"}).store_page(0, "one", "t")
        store.document("two.pdf", {"sha256": "b"}).store_page(0, "two", "t")
        self.assertEqual(set(store.documents()), {"one.pdf", "two.pdf"})
        self.assertEqual(store.document("one.pdf", {"sha256": "a"}).cached_page(0),
                         "one")

    def test_the_checkpoint_file_is_readable_json(self):
        entry = self.store().document("book.pdf", {"sha256": "a"})
        entry.store_page(0, "page", "traditional")
        state = json.loads((self.root / "tmp" / "checkpoint.json").read_text(
            encoding="utf-8"))
        self.assertIn("book.pdf", state["documents"])
        self.assertEqual(state["documents"]["book.pdf"]["pages"]["0"]["method"],
                         "traditional")


class LockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "run.lock"

    def test_a_second_holder_is_refused_while_the_first_holds_it(self):
        with exclusive_lock(self.path):
            with self.assertRaisesRegex(RuntimeError, "Another run"):
                with exclusive_lock(self.path):
                    pass

    def test_lock_position_stays_fixed_when_the_file_grows(self):
        self.path.write_text("previous run", encoding="utf-8")
        with exclusive_lock(self.path) as handle:
            handle.write(" new run")
            handle.flush()
            with self.assertRaisesRegex(RuntimeError, "Another run"):
                with exclusive_lock(self.path):
                    pass

    def test_the_lock_is_released_afterwards(self):
        with exclusive_lock(self.path):
            pass
        with exclusive_lock(self.path):
            pass


if __name__ == "__main__":
    unittest.main()
