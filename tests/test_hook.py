from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "book-translator" / "scripts"))
from progress import activate, initialize_project, load_progress, save_progress

spec = importlib.util.spec_from_file_location("check_progress_hook", ROOT / "hooks" / "check-progress.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        initialize_project(self.project)

    def tearDown(self):
        self.temporary.cleanup()

    def test_unrelated_directory_is_ignored(self):
        self.assertEqual(hook.ALLOW, hook.evaluate({"cwd": self.temporary.name}))

    def test_automatic_work_blocks_stop_but_user_review_does_not(self):
        activate(self.project, {"режим": "продолжить"}, [])
        blocked = hook.evaluate({"cwd": str(self.project)})
        self.assertEqual("block", blocked["decision"])
        progress = load_progress(self.project)
        progress.update({"статус_книги": "ожидает-одобрения", "этап": "пользовательская-верификация"})
        save_progress(self.project, progress)
        self.assertEqual(hook.ALLOW, hook.evaluate({"cwd": str(self.project)}))

    def test_explained_critical_error_allows_stop(self):
        activate(self.project, {"режим": "продолжить"}, [])
        progress = load_progress(self.project)
        progress.update({"статус_книги": "ошибка", "ошибка": "RTF нельзя безопасно собрать."})
        save_progress(self.project, progress)
        self.assertEqual(hook.ALLOW, hook.evaluate({"cwd": str(self.project)}))


if __name__ == "__main__":
    unittest.main()
