from __future__ import annotations

import importlib.util
import json
import os
import subprocess
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

    def test_foreign_active_file_without_project_identity_is_ignored(self):
        foreign = Path(self.temporary.name) / "foreign"
        child = foreign / "nested"
        (foreign / "work").mkdir(parents=True)
        child.mkdir()
        (foreign / "work" / "active.json").write_text("{}", encoding="utf-8")
        self.assertEqual(hook.ALLOW, hook.evaluate({"cwd": str(child)}))

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

    def test_hook_protocol_is_utf8_even_with_legacy_console_encoding(self):
        cyrillic_project = Path(self.temporary.name) / "книга"
        initialize_project(cyrillic_project)
        activate(cyrillic_project, {"режим": "продолжить"}, [])
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "cp1251"
        event = json.dumps({"cwd": str(cyrillic_project)}, ensure_ascii=False).encode("utf-8")
        completed = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "check-progress.py")],
            input=event,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=True,
        )
        response = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual("block", response["decision"])
        self.assertIn("Перевод", response["reason"])


if __name__ == "__main__":
    unittest.main()
