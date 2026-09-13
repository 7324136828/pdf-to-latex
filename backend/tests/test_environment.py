from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

TESTS_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = TESTS_ROOT.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from setup_tools.environment import SetupError, check_python_version


class PythonVersionTests(unittest.TestCase):
    def test_required_python_version_is_accepted(self):
        info = SimpleNamespace(major=3, minor=14, micro=6)

        self.assertIn("Python 3.14.6", check_python_version(info))

    def test_other_python_patch_versions_are_rejected(self):
        for micro in (5, 7):
            with self.subTest(micro=micro), self.assertRaisesRegex(
                SetupError, r"needs Python 3\.14\.6"
            ):
                check_python_version(
                    SimpleNamespace(major=3, minor=14, micro=micro)
                )


if __name__ == "__main__":
    unittest.main()
