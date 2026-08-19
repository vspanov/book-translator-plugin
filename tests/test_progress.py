import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/book-translator/scripts"
sys.path.insert(0, str(SCRIPTS))
import progress


def make_file(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def make_state_directory(path: Path, prefix: str = "new") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name in progress.STATE_ASSETS:
        (path / name).write_text(f"{prefix}: {name}\n", encoding="utf-8")
    return path


def ready_progress(chapter_name: str) -> dict:
    return {
        "версия": 1,
        "статус_книги": "в_работе",
        "этап": "готово",
        "текущая_глава": chapter_name,
        "последняя_готовая_глава": chapter_name,
        "ошибка": None,
    }


class ProjectInitializationTests(unittest.TestCase):
    def test_initialize_project_creates_russian_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            self.assertTrue((project / "output").is_dir())
            self.assertTrue((project / "work").is_dir())
            self.assertIn("Персонажи", (project / "state/characters.md").read_text(encoding="utf-8"))
            state = json.loads((project / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual("не_начат", state["статус_книги"])
            self.assertIsNone(state["текущая_глава"])

    def test_initialize_project_does_not_overwrite_existing_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "state").mkdir()
            existing = project / "state/glossary.md"
            existing.write_text("МОЙ ВАРИАНТ", encoding="utf-8")
            progress.initialize_project(project)
            self.assertEqual("МОЙ ВАРИАНТ", existing.read_text(encoding="utf-8"))


class StageMachineTests(unittest.TestCase):
    def test_stage_cannot_be_skipped_repeated_or_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")

            for invalid in ("проверка_1", "неизвестный"):
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(ValueError, "ожидался"):
                        progress.advance_stage(project, invalid, artifact="report.json")

            progress.advance_stage(project, "извлечение", artifact="source.json")
            with self.assertRaisesRegex(ValueError, "ожидался"):
                progress.advance_stage(project, "извлечение", artifact="source.json")

    def test_failed_verification_does_not_reach_memory_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            progress.advance_stage(project, "извлечение", artifact="source-blocks.json")
            progress.advance_stage(project, "перевод", artifact="draft.json")
            progress.advance_stage(project, "полнота_1", artifact="completeness-1.json")

            progress.record_failure(project, "проверка_1", "Критический пропуск")

            state = progress.load_progress(project)
            self.assertEqual("ошибка", state["статус_книги"])
            self.assertEqual("проверка_1", state["этап"])
            self.assertEqual("Критический пропуск", state["ошибка"])
            self.assertTrue((project / "work/active.json").is_file())
            with self.assertRaisesRegex(ValueError, "ошиб"):
                progress.advance_stage(project, "редактура_1", artifact="edited.json")

    def test_only_one_chapter_can_be_active(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            marker = (project / "work/active.json").read_bytes()

            with self.assertRaisesRegex(ValueError, "уже активна"):
                progress.start_chapter(project, "chapter-2.docx")

            self.assertEqual(marker, (project / "work/active.json").read_bytes())
            active = json.loads(marker)
            self.assertEqual(str(project.resolve()), active["проект"])
            self.assertEqual("chapter-1.docx", active["глава"])
            self.assertIn("время_начала", active)

    def test_second_edit_is_optional_and_third_cycle_is_forbidden(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            stages = (
                "извлечение", "перевод", "полнота_1", "проверка_1",
                "редактура_1", "полнота_2", "проверка_2",
            )
            for stage in stages:
                progress.advance_stage(project, stage, artifact=f"{stage}.json")

            progress.request_second_edit(project)
            with self.assertRaisesRegex(ValueError, "второй цикл"):
                progress.request_second_edit(project)
            progress.advance_stage(project, "редактура_2", artifact="edit-2.json")
            progress.advance_stage(project, "проверка_3", artifact="report-3.json")
            with self.assertRaisesRegex(ValueError, "третий"):
                progress.request_second_edit(project)

            progress.record_failure(project, "проверка_3", "Ошибка осталась")
            self.assertEqual("ошибка", progress.load_progress(project)["статус_книги"])

    def test_active_marker_exists_until_consistent_book_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            self.assertTrue((project / "work/active.json").is_file())

            with self.assertRaisesRegex(ValueError, "не заверш"):
                progress.finish_book(project)

            self.assertTrue((project / "work/active.json").is_file())


class TransactionTests(unittest.TestCase):
    def prepare(self, project: Path, chapter: str = "chapter-1.docx") -> Path:
        return progress.prepare_transaction(
            project,
            chapter_name=chapter,
            built_document=make_file(project / "work/result.docx", b"result"),
            next_state=make_state_directory(project / "work/next-state"),
            next_progress=ready_progress(chapter),
        )

    def test_prepare_is_private_until_ready_and_validates_complete_state(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            old_state = progress.directory_sha256(project / "state")
            old_progress = (project / "progress.json").read_bytes()
            incomplete = project / "work/incomplete-state"
            incomplete.mkdir()

            with self.assertRaisesRegex(ValueError, "памят"):
                progress.prepare_transaction(
                    project,
                    chapter_name="chapter-1.docx",
                    built_document=make_file(project / "work/result.docx", b"result"),
                    next_state=incomplete,
                    next_progress=ready_progress("chapter-1.docx"),
                )

            self.assertEqual(old_state, progress.directory_sha256(project / "state"))
            self.assertEqual(old_progress, (project / "progress.json").read_bytes())
            self.assertFalse((project / "output/chapter-1.docx").exists())

    def test_prepare_rejects_work_link_to_input(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "input"
            source.mkdir()
            linked = project / "work"
            make_directory_link(linked, source)
            try:
                progress.initialize_project(project)
                with self.assertRaisesRegex(ValueError, "небезопас"):
                    progress.prepare_transaction(
                        project,
                        chapter_name="chapter-1.docx",
                        built_document=make_file(source / "result.docx", b"result"),
                        next_state=make_state_directory(source / "next-state"),
                        next_progress=ready_progress("chapter-1.docx"),
                    )
                self.assertFalse((source / "transactions").exists())
            finally:
                if linked.exists():
                    linked.rmdir()

    def test_recovery_finishes_interruptions_without_retranslation(self):
        for interrupted_step in ("state", "output"):
            with self.subTest(interrupted_step=interrupted_step), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                progress.initialize_project(project)
                transaction = self.prepare(project)

                progress.commit_transaction(project, transaction, interrupt_after=interrupted_step)
                progress.recover_transaction(project)

                self.assertEqual(b"result", (project / "output/chapter-1.docx").read_bytes())
                self.assertEqual("chapter-1.docx", progress.load_progress(project)["последняя_готовая_глава"])
                self.assertTrue((transaction / "завершено").is_file())
                self.assertEqual([], progress.check_consistency(project))

    def test_recovery_removes_only_unready_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            temporary = project / "work/transactions/temporary"
            temporary.mkdir(parents=True)
            (temporary / "fragment").write_text("частично", encoding="utf-8")
            input_file = make_file(project / "input/chapter-1.docx", b"original")

            progress.recover_transaction(project)

            self.assertFalse(temporary.exists())
            self.assertEqual(b"original", input_file.read_bytes())

    def test_inconsistent_ready_transaction_rolls_back_published_data(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            old_memory = (project / "state/glossary.md").read_bytes()
            old_progress = (project / "progress.json").read_bytes()
            transaction = self.prepare(project)
            progress.commit_transaction(project, transaction, interrupt_after="state")
            (transaction / "new-output/chapter-1.docx").write_bytes(b"damaged")

            with self.assertRaisesRegex(ValueError, "восстанов"):
                progress.recover_transaction(project)

            self.assertEqual(old_memory, (project / "state/glossary.md").read_bytes())
            self.assertEqual(old_progress, (project / "progress.json").read_bytes())
            self.assertFalse((project / "output/chapter-1.docx").exists())

    def test_consistency_detects_output_and_memory_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            transaction = self.prepare(project)
            progress.commit_transaction(project, transaction)
            (project / "output/chapter-1.docx").write_bytes(b"changed")
            (project / "state/glossary.md").write_text("changed", encoding="utf-8")

            errors = progress.check_consistency(project)

            self.assertTrue(any("результат" in error for error in errors))
            self.assertTrue(any("памят" in error for error in errors))


class FinishAndRestartTests(unittest.TestCase):
    def test_finish_requires_no_unprocessed_chapters_and_removes_active_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            write_manifest = {
                "версия": 1,
                "главы": [{"имя": "chapter-1.docx"}, {"имя": "chapter-2.docx"}],
            }
            progress.write_json_atomic(project / "work/manifest.json", write_manifest)
            transaction = progress.prepare_transaction(
                project,
                chapter_name="chapter-1.docx",
                built_document=make_file(project / "work/result.docx", b"result"),
                next_state=make_state_directory(project / "work/next-state"),
                next_progress=ready_progress("chapter-1.docx"),
            )
            progress.commit_transaction(project, transaction)

            with self.assertRaisesRegex(ValueError, "необработ"):
                progress.finish_book(project)
            self.assertTrue((project / "work/active.json").exists())

            progress.write_json_atomic(
                project / "work/manifest.json",
                {"версия": 1, "главы": [{"имя": "chapter-1.docx"}, "chapter-2.docx"]},
            )
            with self.assertRaisesRegex(ValueError, "Манифест"):
                progress.finish_book(project)

            progress.write_json_atomic(
                project / "work/manifest.json",
                {"версия": 1, "главы": [{"имя": "chapter-1.docx"}]},
            )
            progress.finish_book(project)
            self.assertEqual("готово", progress.load_progress(project)["статус_книги"])
            self.assertFalse((project / "work/active.json").exists())

    def test_restart_preview_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            output = make_file(project / "output/result.docx", b"translated")
            before = output.read_bytes()

            affected = progress.restart_project(project)

            self.assertIn(project / "output", affected)
            self.assertEqual(before, output.read_bytes())
            self.assertFalse((project / "work/restarts").exists())

    def test_restart_backs_up_results_and_work_but_never_touches_input(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            original = make_file(project / "input/chapter-1.docx", b"original")
            make_file(project / "output/result.docx", b"translated")
            make_file(project / "work/stage/report.json", b"report")

            backup = progress.restart_project(project, confirmed=True)

            self.assertEqual(b"original", original.read_bytes())
            self.assertEqual(b"translated", (backup / "output/result.docx").read_bytes())
            self.assertEqual(b"report", (backup / "work/stage/report.json").read_bytes())
            self.assertEqual([], list((project / "output").iterdir()))
            self.assertEqual("не_начат", progress.load_progress(project)["статус_книги"])
            self.assertEqual(
                hashlib.sha256(b"original").hexdigest(),
                hashlib.sha256(original.read_bytes()).hexdigest(),
            )

    def test_restart_rejects_work_link_to_input(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            original = make_file(project / "input/chapter-1.docx", b"original")
            linked = project / "work/linked-input"
            make_directory_link(linked, project / "input")
            try:
                with self.assertRaisesRegex(ValueError, "небезопас"):
                    progress.restart_project(project, confirmed=True)

                self.assertEqual(b"original", original.read_bytes())
            finally:
                if linked.exists():
                    linked.rmdir()


if __name__ == "__main__":
    unittest.main()
