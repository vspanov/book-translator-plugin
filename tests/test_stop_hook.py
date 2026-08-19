import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "check-progress.py"
ALLOW = {"continue": True, "suppressOutput": True}
SCRIPTS = ROOT / "skills" / "book-translator" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import progress


def run_hook(
    cwd: Path,
    event: dict | None = None,
    raw_input: bytes | None = None,
    environment: dict | None = None,
    hook: Path = HOOK,
) -> dict:
    completed = subprocess.run(
        [sys.executable, str(hook)],
        cwd=cwd,
        input=raw_input or json.dumps(
            event or {"cwd": str(cwd)}, ensure_ascii=False
        ).encode("utf-8"),
        capture_output=True,
        check=True,
        env=environment,
    )
    return json.loads(completed.stdout.decode("utf-8"))


def activate(project: Path, state: dict | None = None) -> None:
    (project / "work").mkdir()
    (project / "work" / "active.json").write_text("{}", encoding="utf-8")
    if state is not None:
        (project / "progress.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )


def completed_project(project: Path) -> None:
    progress.initialize_project(project)
    progress.start_chapter(project, "chapter-1.docx")
    next_state = project / "work" / "next-state"
    next_state.mkdir()
    for name in progress.STATE_ASSETS:
        (next_state / name).write_text(name, encoding="utf-8")
    checkpoint = progress.load_progress(project)
    checkpoint.update({
        "статус_книги": "в_работе",
        "этап": "готово",
        "последняя_готовая_глава": "chapter-1.docx",
        "ошибка": None,
    })
    result = project / "work" / "result.docx"
    result.write_bytes(b"result")
    transaction = progress.prepare_transaction(
        project,
        "chapter-1.docx",
        result,
        next_state,
        checkpoint,
    )
    progress.commit_transaction(project, transaction)
    state = progress.load_progress(project)
    state.update({"статус_книги": "готово", "необработанных_глав": 0})
    progress.write_json_atomic(project / "progress.json", state)


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

    def test_reads_russian_cwd_and_writes_russian_json_as_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "книга"
            project.mkdir()
            activate(project, {
                "статус_книги": "в_работе",
                "этап": "перевод",
                "текущая_глава": "глава-1.docx",
                "ошибка": None,
                "необработанных_глав": 1,
            })
            environment = os.environ | {"PYTHONIOENCODING": "cp1251"}

            result = run_hook(project, environment=environment)

            self.assertEqual("block", result["decision"])
            self.assertIn("глава-1.docx", result["reason"])

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

    def test_blocks_completion_when_manifest_has_unprocessed_chapter(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            completed_project(project)
            (project / "work" / "manifest.json").write_text(json.dumps({
                "версия": 1,
                "главы": [{"имя": "chapter-1.docx"}, {"имя": "chapter-2.docx"}],
            }, ensure_ascii=False), encoding="utf-8")

            result = run_hook(project)

            self.assertEqual("block", result["decision"])
            self.assertIn("очеред", result["reason"].lower())

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
                    input=json.dumps({"cwd": str(project)}).encode("utf-8"),
                    capture_output=True,
                    check=True,
                )
                result = json.loads(completed.stdout.decode("utf-8"))

                self.assertEqual("block", result["decision"])
                self.assertNotIn(b"Traceback", completed.stderr)

    def test_bad_stdin_blocks_when_current_folder_is_active_project(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            activate(project)

            result = run_hook(project, raw_input=b"{")

            self.assertEqual("block", result["decision"])

    def test_allows_malformed_cwd_when_no_active_project_is_known(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_hook(Path(directory), {"cwd": "\0"})

            self.assertEqual(ALLOW, result)

    def test_blocks_malformed_active_marker_despite_explained_error(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            activate(project, {
                "статус_книги": "ошибка",
                "этап": "проверка_2",
                "текущая_глава": "chapter-1.docx",
                "ошибка": "Критический пропуск зафиксирован для исправления.",
                "необработанных_глав": 1,
            })
            (project / "work" / "active.json").write_text("{", encoding="utf-8")

            result = run_hook(project)

            self.assertEqual("block", result["decision"])
            self.assertIn("маркер", result["reason"].lower())

    def test_import_does_not_create_bytecode_in_clean_plugin_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = Path(directory) / "plugin"
            shutil.copytree(ROOT, plugin, ignore=shutil.ignore_patterns("__pycache__"))
            environment = os.environ.copy()
            environment.pop("PYTHONDONTWRITEBYTECODE", None)

            self.assertEqual(
                ALLOW,
                run_hook(
                    plugin,
                    environment=environment,
                    hook=plugin / "hooks" / "check-progress.py",
                ),
            )
            self.assertEqual([], list(plugin.rglob("__pycache__")))


if __name__ == "__main__":
    unittest.main()
