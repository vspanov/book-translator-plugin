import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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


def make_file_link(link: Path, target: Path) -> None:
    link.symlink_to(target)


def remove_directory_link(link: Path) -> None:
    link.unlink() if link.is_symlink() else link.rmdir()


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
            self.assertIn(
                "Персонажи",
                (project / "state/characters.md").read_text(encoding="utf-8"),
            )
            state = json.loads((project / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual("не_начат", state["статус_книги"])
            self.assertIsNone(state["текущая_глава"])
            identity = json.loads(
                (project / "work/book-translator.json").read_text(encoding="utf-8")
            )
            self.assertEqual({"тип": "book-translator", "версия": 1}, identity)

    def test_initialize_project_does_not_overwrite_existing_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "state").mkdir()
            existing = project / "state/glossary.md"
            existing.write_text("МОЙ ВАРИАНТ", encoding="utf-8")
            progress.initialize_project(project)
            self.assertEqual("МОЙ ВАРИАНТ", existing.read_text(encoding="utf-8"))

    def test_initialize_project_rejects_boolean_and_float_identity_versions(self):
        for invalid_version in (True, 1.0):
            with self.subTest(version=invalid_version), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                marker = project / "work/book-translator.json"
                marker.parent.mkdir()
                marker.write_text(
                    json.dumps(
                        {"тип": "book-translator", "версия": invalid_version},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                marker_before = marker.read_bytes()

                with self.assertRaisesRegex(ValueError, "не принадлежит"):
                    progress.initialize_project(project)

                self.assertEqual(marker_before, marker.read_bytes())

    def test_atomic_json_ignores_predictable_hardlink_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            original = make_file(project / "input/original.json", b"original")
            os.link(original, project / "progress.json.tmp")

            progress.write_json_atomic(project / "progress.json", {"версия": 1})

            self.assertEqual(b"original", original.read_bytes())


class StageMachineTests(unittest.TestCase):
    def write_manifest(self, project: Path, *names: str) -> None:
        progress.write_json_atomic(
            project / "work/manifest.json",
            {"версия": 1, "главы": [{"имя": name} for name in names]},
        )

    def commit_first_manifest_chapter(self, project: Path) -> None:
        progress.start_chapter(project, "chapter-1.docx")
        transaction = progress.prepare_transaction(
            project,
            "chapter-1.docx",
            make_file(project / "work/chapter-1.docx", b"result"),
            make_state_directory(project / "work/next-state"),
            ready_progress("chapter-1.docx"),
        )
        progress.commit_transaction(project, transaction)

    def test_manifest_rejects_skipping_the_first_chapter(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            self.write_manifest(project, "chapter-1.docx", "chapter-2.docx")

            with self.assertRaisesRegex(ValueError, "chapter-1"):
                progress.start_chapter(project, "chapter-2.docx")

    def test_manifest_rejects_completed_and_unknown_chapters(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            self.write_manifest(project, "chapter-1.docx", "chapter-2.docx")
            self.commit_first_manifest_chapter(project)

            with self.assertRaisesRegex(ValueError, "уже завершена"):
                progress.start_chapter(project, "chapter-1.docx")
            with self.assertRaisesRegex(ValueError, "неизвест"):
                progress.start_chapter(project, "chapter-9.docx")

    def test_manifest_allows_only_normal_first_then_second_order(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            self.write_manifest(project, "chapter-1.docx", "chapter-2.docx")
            self.commit_first_manifest_chapter(project)

            progress.start_chapter(project, "chapter-2.docx")

            self.assertEqual("chapter-2.docx", progress.load_progress(project)["текущая_глава"])

    def test_empty_or_malformed_manifest_is_rejected(self):
        for manifest in ({"версия": 1, "главы": []}, {"версия": 1}, {"версия": 1, "главы": [None]}):
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                progress.initialize_project(project)
                progress.write_json_atomic(project / "work/manifest.json", manifest)

                with self.assertRaisesRegex(ValueError, "Манифест"):
                    progress.start_chapter(project, "chapter-1.docx")

    def test_start_chapter_recovers_marker_written_before_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            progress.write_json_atomic(
                project / "progress.json", ready_progress("chapter-1.docx")
            )
            real_write = progress.write_json_atomic
            write_number = 0

            def fail_second_write(path, value):
                nonlocal write_number
                write_number += 1
                if write_number == 2:
                    raise OSError("искусственный сбой записи progress")
                real_write(path, value)

            with (
                mock.patch.object(progress, "write_json_atomic", fail_second_write),
                self.assertRaises(OSError),
            ):
                progress.start_chapter(project, "chapter-2.docx")

            marker_path = project / "work/active.json"
            progress_path = project / "progress.json"
            marker_after_crash = marker_path.read_bytes()
            progress_after_crash = progress_path.read_bytes()
            self.assertEqual("chapter-2.docx", json.loads(marker_after_crash)["глава"])
            self.assertEqual(
                "chapter-1.docx", json.loads(progress_after_crash)["текущая_глава"]
            )

            with self.assertRaisesRegex(ValueError, "частично|актив"):
                progress.start_chapter(project, "chapter-3.docx")

            self.assertEqual(marker_after_crash, marker_path.read_bytes())
            self.assertEqual(progress_after_crash, progress_path.read_bytes())

            progress.start_chapter(project, "chapter-2.docx")

            self.assertEqual("chapter-2.docx", json.loads(marker_path.read_bytes())["глава"])
            state = progress.load_progress(project)
            self.assertEqual("chapter-2.docx", state["текущая_глава"])
            self.assertEqual("ожидает_извлечения", state["этап"])

    def test_first_start_retries_progress_write_without_replacing_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            self.write_manifest(project, "chapter-1.docx", "chapter-2.docx")
            real_write = progress.write_json_atomic
            write_number = 0

            def fail_second_write(path, value):
                nonlocal write_number
                write_number += 1
                if write_number == 2:
                    raise OSError("искусственный сбой первой записи progress")
                real_write(path, value)

            with (
                mock.patch.object(progress, "write_json_atomic", fail_second_write),
                self.assertRaises(OSError),
            ):
                progress.start_chapter(project, "chapter-1.docx")

            marker_path = project / "work/active.json"
            marker_after_crash = marker_path.read_bytes()
            self.assertEqual("не_начат", progress.load_progress(project)["статус_книги"])

            with self.assertRaisesRegex(ValueError, "chapter-1|частично"):
                progress.start_chapter(project, "chapter-2.docx")
            self.assertEqual(marker_after_crash, marker_path.read_bytes())

            progress.start_chapter(project, "chapter-1.docx")

            self.assertEqual(marker_after_crash, marker_path.read_bytes())
            state = progress.load_progress(project)
            self.assertEqual("chapter-1.docx", state["текущая_глава"])
            self.assertEqual("ожидает_извлечения", state["этап"])

    def test_first_start_recovery_rejects_damaged_or_foreign_marker(self):
        markers = (
            "{",
            json.dumps({"проект": "другой проект", "глава": "chapter-1.docx"}),
        )
        for marker in markers:
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                progress.initialize_project(project)
                self.write_manifest(project, "chapter-1.docx")
                (project / "work/active.json").write_text(marker, encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "маркер|Маркер"):
                    progress.start_chapter(project, "chapter-1.docx")

                self.assertEqual("не_начат", progress.load_progress(project)["статус_книги"])

    def test_first_start_recovery_rejects_marker_without_project_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            self.write_manifest(project, "chapter-1.docx")
            marker_path = project / "work/active.json"
            marker_path.write_text(
                json.dumps(
                    {"проект": str(project.resolve()), "глава": "chapter-1.docx"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            marker_before = marker_path.read_bytes()
            progress_before = (project / "progress.json").read_bytes()

            with self.assertRaisesRegex(ValueError, "маркер|Маркер|принадлеж"):
                progress.start_chapter(project, "chapter-1.docx")

            self.assertEqual(marker_before, marker_path.read_bytes())
            self.assertEqual(progress_before, (project / "progress.json").read_bytes())

    def test_first_start_recovery_rejects_boolean_and_float_identity_versions(self):
        for invalid_version in (True, 1.0):
            with self.subTest(version=invalid_version), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                progress.initialize_project(project)
                self.write_manifest(project, "chapter-1.docx")
                marker_path = project / "work/active.json"
                marker_path.write_text(
                    json.dumps(
                        {
                            "тип": "book-translator",
                            "версия": invalid_version,
                            "проект": str(project.resolve()),
                            "глава": "chapter-1.docx",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                marker_before = marker_path.read_bytes()
                progress_before = (project / "progress.json").read_bytes()

                with self.assertRaisesRegex(ValueError, "не принадлежит"):
                    progress.start_chapter(project, "chapter-1.docx")

                self.assertEqual(marker_before, marker_path.read_bytes())
                self.assertEqual(progress_before, (project / "progress.json").read_bytes())

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
            self.assertEqual("book-translator", active["тип"])
            self.assertEqual(1, active["версия"])
            self.assertIn("время_начала", active)

    def test_missing_active_marker_does_not_allow_overwriting_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            (project / "work/active.json").unlink()

            with self.assertRaisesRegex(ValueError, "незаверш"):
                progress.start_chapter(project, "chapter-2.docx")

            self.assertEqual("chapter-1.docx", progress.load_progress(project)["текущая_глава"])

    def test_recorded_error_does_not_allow_starting_next_chapter(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            state = progress.load_progress(project)
            state.update({"статус_книги": "ошибка", "этап": "готово", "ошибка": "Сбой"})
            progress.write_json_atomic(project / "progress.json", state)

            with self.assertRaisesRegex(ValueError, "ошиб"):
                progress.start_chapter(project, "chapter-2.docx")

            self.assertEqual("chapter-1.docx", progress.load_progress(project)["текущая_глава"])

    def test_dot_chapter_names_are_rejected(self):
        for chapter_name in (".", ".."):
            with (
                self.subTest(chapter_name=chapter_name),
                tempfile.TemporaryDirectory() as directory,
            ):
                project = Path(directory)
                progress.initialize_project(project)
                with self.assertRaisesRegex(ValueError, "Имя главы"):
                    progress.start_chapter(project, chapter_name)

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
            built_document=make_file(project / "work" / chapter, b"result"),
            next_state=make_state_directory(project / "work/next-state"),
            next_progress=ready_progress(chapter),
        )

    def test_source_chapter_and_published_result_names_are_independent(self):
        cases = (
            ("chapter-1.docx", "chapter-1.pages"),
            ("chapter-1.pages", "chapter-1.docx"),
            ("chapter-1.docx", "chapter-1.docx"),
        )
        for chapter_name, result_name in cases:
            with self.subTest(chapter_name=chapter_name, result_name=result_name), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                progress.initialize_project(project)
                transaction = progress.prepare_transaction(
                    project,
                    chapter_name=chapter_name,
                    built_document=make_file(project / "work" / result_name, b"result"),
                    next_state=make_state_directory(project / "work/next-state"),
                    next_progress=ready_progress(chapter_name),
                )

                metadata = json.loads((transaction / "transaction.json").read_text(encoding="utf-8"))
                self.assertEqual(chapter_name, metadata["исходная_глава"])
                self.assertEqual(result_name, metadata["имя_результата"])
                progress.commit_transaction(project, transaction)

                self.assertEqual(b"result", (project / "output" / result_name).read_bytes())
                self.assertEqual(chapter_name, progress.load_progress(project)["последняя_готовая_глава"])
                self.assertEqual([], progress.check_completed_chapter(project, chapter_name))

    def test_pages_package_survives_commit_recovery_check_and_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.write_json_atomic(
                project / "work/manifest.json",
                {"версия": 1, "главы": [{"имя": "chapter-1.docx"}]},
            )
            progress.start_chapter(project, "chapter-1.docx")
            package = project / "work/chapter-1.pages"
            make_file(package / "Index.zip", b"pages package")
            transaction = progress.prepare_transaction(
                project,
                chapter_name="chapter-1.docx",
                built_document=package,
                next_state=make_state_directory(project / "work/next-state"),
                next_progress=ready_progress("chapter-1.docx"),
            )

            progress.commit_transaction(project, transaction, interrupt_after="output")
            progress.recover_transaction(project)

            published = project / "output/chapter-1.pages"
            self.assertTrue(published.is_dir())
            self.assertEqual(b"pages package", (published / "Index.zip").read_bytes())
            self.assertEqual([], progress.check_completed_chapter(project, "chapter-1.docx"))
            progress.finish_book(project)
            self.assertEqual("готово", progress.load_progress(project)["статус_книги"])
            self.assertEqual(b"pages package", (published / "Index.zip").read_bytes())

    def test_pages_package_rejects_internal_directory_link_before_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            package = project / "work/chapter-1.pages"
            make_file(package / "Index.zip", b"pages package")
            external = project / "input"
            sentinel = make_file(external / "original.docx", b"original")
            linked = package / "external"
            make_directory_link(linked, external)
            try:
                with self.assertRaisesRegex(ValueError, "ссыл|Ссыл|небезопас"):
                    progress.prepare_transaction(
                        project,
                        chapter_name="chapter-1.docx",
                        built_document=package,
                        next_state=make_state_directory(project / "work/next-state"),
                        next_progress=ready_progress("chapter-1.docx"),
                    )
                self.assertEqual(b"original", sentinel.read_bytes())
                self.assertFalse((project / "work/transactions").exists())
            finally:
                if linked.exists():
                    remove_directory_link(linked)

    def test_completed_pages_package_detects_content_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            package = project / "work/chapter-1.pages"
            make_file(package / "Index.zip", b"pages package")
            transaction = progress.prepare_transaction(
                project,
                chapter_name="chapter-1.docx",
                built_document=package,
                next_state=make_state_directory(project / "work/next-state"),
                next_progress=ready_progress("chapter-1.docx"),
            )
            progress.commit_transaction(project, transaction)

            (project / "output/chapter-1.pages/Index.zip").write_bytes(b"tampered")

            errors = progress.check_completed_chapter(project, "chapter-1.docx")
            self.assertTrue(any("сумм" in error.lower() for error in errors))

    def test_completed_pages_package_reports_internal_directory_link(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            package = project / "work/chapter-1.pages"
            make_file(package / "Index.zip", b"pages package")
            transaction = progress.prepare_transaction(
                project,
                chapter_name="chapter-1.docx",
                built_document=package,
                next_state=make_state_directory(project / "work/next-state"),
                next_progress=ready_progress("chapter-1.docx"),
            )
            progress.commit_transaction(project, transaction)
            external = project / "input"
            make_file(external / "original.docx", b"original")
            linked = project / "output/chapter-1.pages/external"
            make_directory_link(linked, external)
            try:
                errors = progress.check_completed_chapter(project, "chapter-1.docx")
                self.assertTrue(any("ссыл" in error.lower() for error in errors))
            finally:
                if linked.exists():
                    remove_directory_link(linked)

    def test_pages_package_is_removed_when_interrupted_transaction_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            old_progress = (project / "progress.json").read_bytes()
            old_state = progress.directory_sha256(project / "state")
            existing = make_file(project / "output/existing.docx", b"existing")
            package = project / "work/chapter-1.pages"
            make_file(package / "Index.zip", b"pages package")
            transaction = progress.prepare_transaction(
                project,
                chapter_name="chapter-1.docx",
                built_document=package,
                next_state=make_state_directory(project / "work/next-state"),
                next_progress=ready_progress("chapter-1.docx"),
            )
            progress.commit_transaction(project, transaction, interrupt_after="output")
            (transaction / "next-progress.json").write_bytes(b"{}")

            with self.assertRaisesRegex(ValueError, "восстанов"):
                progress.commit_transaction(project, transaction)

            self.assertFalse((project / "output/chapter-1.pages").exists())
            self.assertEqual(b"existing", existing.read_bytes())
            self.assertEqual(old_progress, (project / "progress.json").read_bytes())
            self.assertEqual(old_state, progress.directory_sha256(project / "state"))

    def test_prepare_rejects_directory_result_without_pages_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            directory_result = project / "work/chapter-1.docx"
            make_file(directory_result / "content.bin", b"not a docx file")

            with self.assertRaisesRegex(ValueError, "документ|результат|Pages"):
                progress.prepare_transaction(
                    project,
                    chapter_name="chapter-1.docx",
                    built_document=directory_result,
                    next_state=make_state_directory(project / "work/next-state"),
                    next_progress=ready_progress("chapter-1.docx"),
                )

    def test_prepare_accepts_only_a_safe_explicit_result_name(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            built = make_file(project / "work/assembled.tmp", b"result")
            next_state = make_state_directory(project / "work/next-state")
            checkpoint = ready_progress("chapter-1.docx")

            transaction = progress.prepare_transaction(
                project, "chapter-1.docx", built, next_state, checkpoint,
                result_name="chapter-1.pages",
            )
            metadata = json.loads((transaction / "transaction.json").read_text(encoding="utf-8"))
            self.assertEqual("chapter-1.pages", metadata["имя_результата"])

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            with self.assertRaisesRegex(ValueError, "Имя результата"):
                progress.prepare_transaction(
                    project,
                    "chapter-1.docx",
                    make_file(project / "work/result.docx", b"result"),
                    make_state_directory(project / "work/next-state"),
                    ready_progress("chapter-1.docx"),
                    result_name="../outside.docx",
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
            progress.initialize_project(project)
            source = project / "input"
            source.mkdir()
            linked = project / "work"
            safe_work = project / "work-safe"
            linked.rename(safe_work)
            make_directory_link(linked, source)
            try:
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
                    remove_directory_link(linked)
                safe_work.rename(linked)

    def test_prepare_rejects_transactions_link_to_input(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            source = project / "input"
            sentinel = make_file(source / "original.docx", b"original")
            linked = project / "work/transactions"
            make_directory_link(linked, source)
            try:
                with self.assertRaisesRegex(ValueError, "transactions.*небезопас"):
                    self.prepare(project)
                self.assertEqual(b"original", sentinel.read_bytes())
                self.assertEqual(["original.docx"], [path.name for path in source.iterdir()])
            finally:
                if linked.exists():
                    remove_directory_link(linked)

    def test_recovery_rejects_transaction_link_to_input(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            transactions = project / "work/transactions"
            transactions.mkdir()
            source = project / "input"
            sentinel = make_file(source / "original.docx", b"original")
            linked = transactions / "danger"
            make_directory_link(linked, source)
            try:
                with self.assertRaisesRegex(ValueError, "транзакц.*небезопас"):
                    progress.recover_transaction(project)
                self.assertEqual(b"original", sentinel.read_bytes())
            finally:
                if linked.exists():
                    remove_directory_link(linked)

    def test_prepare_rejects_incomplete_next_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            checkpoint = ready_progress("chapter-1.docx")
            del checkpoint["версия"]

            with self.assertRaisesRegex(ValueError, "контрольн.*неполн"):
                progress.prepare_transaction(
                    project,
                    chapter_name="chapter-1.docx",
                    built_document=make_file(project / "work/result.docx", b"result"),
                    next_state=make_state_directory(project / "work/next-state"),
                    next_progress=checkpoint,
                )

    def test_tampered_next_progress_after_interrupt_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            old_progress = (project / "progress.json").read_bytes()
            old_memory = progress.directory_sha256(project / "state")
            original = make_file(project / "input/original.json", b"original")
            os.link(original, project / "progress.json.rollback.tmp")
            transaction = self.prepare(project)
            progress.commit_transaction(project, transaction, interrupt_after="state")
            tampered = ready_progress("chapter-1.docx")
            tampered["подмена"] = True
            progress.write_json_atomic(transaction / "next-progress.json", tampered)

            with self.assertRaisesRegex(ValueError, "восстанов"):
                progress.recover_transaction(project)

            self.assertEqual(old_progress, (project / "progress.json").read_bytes())
            self.assertEqual(old_memory, progress.directory_sha256(project / "state"))
            self.assertFalse((transaction / "завершено").exists())
            self.assertEqual(b"original", original.read_bytes())

    def test_recovery_finishes_interruptions_without_retranslation(self):
        for interrupted_step in ("state", "output"):
            with (
                self.subTest(interrupted_step=interrupted_step),
                tempfile.TemporaryDirectory() as directory,
            ):
                project = Path(directory)
                progress.initialize_project(project)
                transaction = self.prepare(project)

                progress.commit_transaction(project, transaction, interrupt_after=interrupted_step)
                progress.recover_transaction(project)

                self.assertEqual(b"result", (project / "output/chapter-1.docx").read_bytes())
                self.assertEqual(
                    "chapter-1.docx",
                    progress.load_progress(project)["последняя_готовая_глава"],
                )
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

    def test_completed_chapter_check_detects_output_or_transaction_changes(self):
        for changed_path in ("output", "transaction"):
            with self.subTest(changed_path=changed_path), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                progress.initialize_project(project)
                transaction = self.prepare(project)
                progress.commit_transaction(project, transaction)
                if changed_path == "output":
                    (project / "output/chapter-1.docx").write_bytes(b"changed")
                else:
                    (transaction / "transaction.json").write_text("{}", encoding="utf-8")

                errors = progress.check_completed_chapter(project, "chapter-1.docx")

                self.assertTrue(errors)

    def test_completed_chapter_rejects_linked_new_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            progress.initialize_project(project)
            transaction = self.prepare(project)
            progress.commit_transaction(project, transaction)
            external = root / "external-output"
            make_file(external / "chapter-1.docx", b"outside")
            (transaction / "new-output/chapter-1.docx").unlink()
            (transaction / "new-output").rmdir()
            linked = transaction / "new-output"
            try:
                make_directory_link(linked, external)
            except OSError as error:
                self.skipTest(f"Невозможно создать ссылку: {error}")

            try:
                errors = progress.check_completed_chapter(project, "chapter-1.docx")
                self.assertTrue(any("небезопас" in error for error in errors))
            finally:
                remove_directory_link(linked)

    def test_completed_chapter_rejects_linked_transaction_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            progress.initialize_project(project)
            transaction = self.prepare(project)
            progress.commit_transaction(project, transaction)
            external = make_file(
                root / "external-transaction.json",
                (transaction / "transaction.json").read_bytes(),
            )
            metadata = transaction / "transaction.json"
            metadata.unlink()
            try:
                make_file_link(metadata, external)
            except OSError as error:
                self.skipTest(f"Невозможно создать ссылку: {error}")

            errors = progress.check_completed_chapter(project, "chapter-1.docx")
            self.assertTrue(any("небезопас" in error for error in errors))

    def test_completed_chapter_rejects_linked_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            progress.initialize_project(project)
            transaction = self.prepare(project)
            progress.commit_transaction(project, transaction)
            external = make_file(
                root / "external-checkpoint.json",
                (transaction / "next-progress.json").read_bytes(),
            )
            checkpoint = transaction / "next-progress.json"
            checkpoint.unlink()
            try:
                make_file_link(checkpoint, external)
            except OSError as error:
                self.skipTest(f"Невозможно создать ссылку: {error}")

            errors = progress.check_completed_chapter(project, "chapter-1.docx")
            self.assertTrue(any("небезопас" in error for error in errors))

    def test_commit_rejects_linked_prepared_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            progress.initialize_project(project)
            transaction = self.prepare(project)
            external = root / "external-output"
            make_file(external / "chapter-1.docx", b"outside")
            (transaction / "new-output/chapter-1.docx").unlink()
            (transaction / "new-output").rmdir()
            linked = transaction / "new-output"
            try:
                make_directory_link(linked, external)
            except OSError as error:
                self.skipTest(f"Невозможно создать ссылку: {error}")

            try:
                with self.assertRaisesRegex(ValueError, "небезопас"):
                    progress.commit_transaction(project, transaction)
            finally:
                remove_directory_link(linked)

    def test_consistency_rejects_truncated_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.write_json_atomic(project / "progress.json", {"версия": 1})

            errors = progress.check_consistency(project)

            self.assertTrue(any("progress" in error or "контроль" in error for error in errors))

    def test_consistency_rejects_output_link_outside_project(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            progress.initialize_project(project)
            transaction = self.prepare(project)
            progress.commit_transaction(project, transaction)
            result = (project / "output/chapter-1.docx").read_bytes()
            (project / "output/chapter-1.docx").unlink()
            (project / "output").rmdir()
            external = base / "external-output"
            make_file(external / "chapter-1.docx", result)
            linked = project / "output"
            make_directory_link(linked, external)
            try:
                errors = progress.check_consistency(project)
                self.assertTrue(any("небезопас" in error or "ссыл" in error for error in errors))
            finally:
                remove_directory_link(linked)


class FinishAndRestartTests(unittest.TestCase):
    def test_finish_rejects_recorded_error_and_keeps_active_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            state = progress.load_progress(project)
            state.update({"статус_книги": "ошибка", "этап": "готово", "ошибка": "Сбой"})
            progress.write_json_atomic(project / "progress.json", state)

            with self.assertRaisesRegex(ValueError, "ошиб"):
                progress.finish_book(project)

            self.assertTrue((project / "work/active.json").exists())

    def test_finish_requires_every_manifest_chapter_to_be_published(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-2.docx")
            progress.write_json_atomic(
                project / "work/manifest.json",
                {"версия": 1, "главы": [{"имя": "chapter-1.docx"}, {"имя": "chapter-2.docx"}]},
            )
            transaction = progress.prepare_transaction(
                project,
                chapter_name="chapter-2.docx",
                built_document=make_file(project / "work/result.docx", b"result-2"),
                next_state=make_state_directory(project / "work/next-state"),
                next_progress=ready_progress("chapter-2.docx"),
            )
            progress.commit_transaction(project, transaction)

            with self.assertRaisesRegex(ValueError, "chapter-1"):
                progress.finish_book(project)

            self.assertTrue((project / "work/active.json").exists())

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
            self.assertTrue((project / "work/book-translator.json").is_file())

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
                {"тип": "book-translator", "версия": 1},
                json.loads((project / "work/book-translator.json").read_text(encoding="utf-8")),
            )
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
                    remove_directory_link(linked)


if __name__ == "__main__":
    unittest.main()
