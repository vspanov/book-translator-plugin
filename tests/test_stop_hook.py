import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "check-progress.py"
ALLOW = {"continue": True, "suppressOutput": True}


def run_hook(cwd: Path, event: dict | None = None, raw_input: str | None = None) -> dict:
    completed = subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=cwd,
        input=raw_input if raw_input is not None else json.dumps(event or {"cwd": str(cwd)}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def activate(project: Path, state: dict | None = None) -> None:
    (project / "work").mkdir()
    (project / "work" / "active.json").write_text("{}", encoding="utf-8")
    if state is not None:
        (project / "progress.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )


class StopHookTests(unittest.TestCase):
    def test_allows_ordinary_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(ALLOW, run_hook(Path(directory)))

    def test_blocks_intermediate_stage_and_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            state = {
                "статус_книги": "в_работе",
                "этап": "перевод",
                "текущая_глава": "chapter-1.docx",
                "ошибка": None,
                "необработанных_глав": 1,
            }
            activate(project, state)
            active_before = (project / "work" / "active.json").read_bytes()
            progress_before = (project / "progress.json").read_bytes()

            result = run_hook(project)

            self.assertEqual("block", result["decision"])
            self.assertIn("chapter-1.docx", result["reason"])
            self.assertIn("перевод", result["reason"])
            self.assertEqual(active_before, (project / "work" / "active.json").read_bytes())
            self.assertEqual(progress_before, (project / "progress.json").read_bytes())

    def test_allows_explained_error(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            activate(project, {
                "статус_книги": "ошибка",
                "этап": "проверка_2",
                "текущая_глава": "chapter-1.docx",
                "ошибка": "Критический пропуск зафиксирован для исправления.",
                "необработанных_глав": 1,
            })

            self.assertEqual(ALLOW, run_hook(project))

    def test_blocks_error_without_explanation(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            activate(project, {
                "статус_книги": "ошибка",
                "этап": "проверка_2",
                "текущая_глава": "chapter-1.docx",
                "ошибка": " ",
                "необработанных_глав": 1,
            })

            result = run_hook(project)

            self.assertEqual("block", result["decision"])
            self.assertIn("объясн", result["reason"].lower())

    def test_blocks_false_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            activate(project, {
                "версия": 1,
                "статус_книги": "готово",
                "этап": "готово",
                "текущая_глава": "chapter-1.docx",
                "последняя_готовая_глава": "chapter-1.docx",
                "ошибка": None,
                "необработанных_глав": 0,
            })

            result = run_hook(project)

            self.assertEqual("block", result["decision"])
            self.assertIn("согласован", result["reason"].lower())

    def test_rechecks_when_stop_hook_is_already_active(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            activate(project, {
                "статус_книги": "в_работе",
                "этап": "перевод",
                "текущая_глава": "chapter-1.docx",
                "ошибка": None,
                "необработанных_глав": 1,
            })

            result = run_hook(project, {"cwd": str(project), "stop_hook_active": True})

            self.assertEqual("block", result["decision"])

    def test_blocks_missing_or_corrupted_progress_without_traceback(self):
        for content in (None, "{"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                activate(project)
                if content is not None:
                    (project / "progress.json").write_text(content, encoding="utf-8")

                completed = subprocess.run(
                    [sys.executable, str(HOOK)],
                    cwd=project,
                    input=json.dumps({"cwd": str(project)}),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                result = json.loads(completed.stdout)

                self.assertEqual("block", result["decision"])
                self.assertNotIn("Traceback", completed.stderr)

    def test_bad_stdin_blocks_when_current_folder_is_active_project(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            activate(project)

            result = run_hook(project, raw_input="{")

            self.assertEqual("block", result["decision"])


if __name__ == "__main__":
    unittest.main()
