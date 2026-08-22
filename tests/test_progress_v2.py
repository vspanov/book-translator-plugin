from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "book-translator" / "scripts"))
import progress as module
from documents import (
    add_annotations, build_manifest, chapter_id, chat_feedback_to_issue, extract_annotations,
    file_sha256, refresh_manifest, rtf_fingerprints,
)
from progress import (
    STATE_FILES, activate, approve_files, commit_transaction, complete_feedback_revision, initialize_project, load_config,
    execute_verification_cycles, load_progress, output_name, parse_request_arguments, prepare_state,
    prepare_transaction, register_feedback, replace_managed_contribution,
    resolve_chapter, restart_project, scan_published_feedback, scan_queue, verification_schedule,
)


RTF = r"{\rtf1\ansi First scene.\par}"


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        initialize_project(self.project)

    def tearDown(self):
        self.temporary.cleanup()

    def source(self, name="chapter-01.rtf"):
        path = self.project / "input" / name
        path.write_text(RTF, encoding="latin-1")
        return path

    def test_initialization_is_idempotent_and_copies_agents(self):
        custom = self.project / "state" / "glossary.md"
        custom.write_text("ручные данные\n", encoding="utf-8")
        agent = self.project / ".codex" / "agents" / "translator.toml"
        agent.write_text("custom = true\n", encoding="utf-8")
        result = initialize_project(self.project)
        self.assertEqual("ручные данные\n", custom.read_text(encoding="utf-8"))
        self.assertIn("translator.toml", result["конфликты_агентов"])
        self.assertEqual("custom = true\n", agent.read_text(encoding="utf-8"))

    def test_old_project_is_not_migrated(self):
        old = Path(self.temporary.name) / "old"
        old.mkdir(); (old / "progress.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "миграция"):
            initialize_project(old)

    def test_configuration_precedence_and_limits(self):
        self.assertEqual("в-финале", load_config(self.project)["пользовательская_верификация"])
        overrides = parse_request_arguments(["перевести-заново", "циклы=20", "верификация=после-каждого-файла"])
        config = load_config(self.project, overrides)
        self.assertEqual(20, config["максимум_циклов"])
        self.assertEqual("после-каждого-файла", config["пользовательская_верификация"])
        self.assertEqual(5, len(verification_schedule(5)))
        self.assertFalse(verification_schedule(5)[-1]["editor_после"])
        self.assertEqual(1, len(verification_schedule(1)))
        self.assertEqual(20, len(verification_schedule(20)))
        for value in (0, 21):
            with self.assertRaises(ValueError): verification_schedule(value)

    def test_cycles_stop_early_and_never_edit_after_last_verifier(self):
        calls = {"verify": 0, "edit": 0}
        def verifier(draft, number):
            calls["verify"] += 1
            return {"замечания": [] if number == 3 else [{"id": str(number)}]}
        def editor(draft, report, number):
            calls["edit"] += 1
            return draft + 1
        draft, reports = execute_verification_cycles(0, 5, verifier, editor)
        self.assertEqual((2, 3, 2), (draft, len(reports), calls["edit"]))
        execute_verification_cycles(0, 1, lambda draft, number: {"замечания": [{"id": "last"}]}, editor)
        self.assertEqual(2, calls["edit"])

    def test_state_preparation_removes_only_selected_contribution(self):
        first, second = "a1b2c3d4", "b1c2d3e4"
        state_file = self.project / "state" / "glossary.md"
        state_file.write_text(
            "<!-- user:start -->\nПользовательское ё\n<!-- user:end -->\n"
            f"<!-- managed:file:{first}:start -->\nПервый\n<!-- managed:file:{first}:end -->\n"
            f"<!-- managed:file:{second}:start -->\nВторой\n<!-- managed:file:{second}:end -->\n",
            encoding="utf-8",
        )
        prepared = self.project / "work" / "prepared"
        prepare_state(self.project, first, prepared)
        text = (prepared / "glossary.md").read_text(encoding="utf-8")
        self.assertIn("Пользовательское ё", text)
        self.assertNotIn("Первый", text)
        self.assertIn("Второй", text)
        replace_managed_contribution(prepared, first, {"glossary.md": "Новая запись"})
        self.assertIn("Новая запись", (prepared / "glossary.md").read_text(encoding="utf-8"))

    def test_new_files_anywhere_are_queued_and_changed_file_conflicts(self):
        later = self.source("chapter-10.rtf")
        manifest = build_manifest(self.project, [later])
        earlier = self.source("chapter-02.rtf")
        refreshed, queue, conflicts = refresh_manifest(self.project, manifest)
        self.assertEqual([earlier.name], [item["имя"] for item in queue])
        self.assertEqual([], conflicts)
        later.write_text(r"{\rtf1 changed\par}", encoding="latin-1")
        _, queue, conflicts = refresh_manifest(self.project, refreshed)
        self.assertEqual([], conflicts)
        self.assertIn(later.name, [item["имя"] for item in queue])
        _, _, conflicts = refresh_manifest(self.project, refreshed, protected_ids={chapter_id(later)})
        self.assertTrue(conflicts)
        _, queue, conflicts = refresh_manifest(
            self.project, refreshed, allow_changed=later.name, protected_ids={chapter_id(later)},
        )
        self.assertIn(later.name, [item["имя"] for item in queue])
        self.assertEqual([], conflicts)

    def test_invalid_new_source_is_not_baselined_and_unstarted_change_is_requeued(self):
        source = self.project / "input" / "chapter-04.rtf"
        source.write_text(r"{\rtf1 broken", encoding="latin-1")
        manifest_path = self.project / "work" / "manifest.json"
        with self.assertRaisesRegex(ValueError, "не прошли проверку"):
            scan_queue(self.project)
        self.assertFalse(manifest_path.exists())

        source.write_text(r"{\rtf1\ansi First draft.\par}", encoding="latin-1")
        _, queue, conflicts = scan_queue(self.project)
        self.assertEqual([], conflicts)
        self.assertEqual([source.name], [item["имя"] for item in queue])
        first_digest = json.loads(manifest_path.read_text(encoding="utf-8"))["главы"][0]["sha256"]

        source.write_text(r"{\rtf1\ansi Second draft.\par}", encoding="latin-1")
        _, queue, conflicts = scan_queue(self.project)
        self.assertEqual([], conflicts)
        self.assertEqual([source.name], [item["имя"] for item in queue])
        second_digest = json.loads(manifest_path.read_text(encoding="utf-8"))["главы"][0]["sha256"]
        self.assertNotEqual(first_digest, second_digest)

    def test_completed_project_reopens_when_new_file_appears(self):
        first = self.source("chapter-03.rtf")
        _, queue, conflicts = scan_queue(self.project)
        self.assertEqual([first.name], [item["имя"] for item in queue])
        self.assertEqual([], conflicts)
        self._publish(first.name)
        inserted = self.source("chapter-01.rtf")
        _, queue, conflicts = scan_queue(self.project)
        self.assertEqual([inserted.name], [item["имя"] for item in queue])
        self.assertEqual([], conflicts)

    def test_final_review_mode_continues_queue(self):
        first = self.source("chapter-01.rtf"); self.source("chapter-02.rtf")
        _, queue, _ = scan_queue(self.project)
        activate(self.project, {"пользовательская_верификация": "в-финале"}, queue)
        self._publish(first.name)
        progress = load_progress(self.project)
        self.assertEqual("в-работе", progress["статус_книги"])
        self.assertEqual(1, len(progress["очередь"]))

    def test_per_file_review_mode_waits_before_next_file(self):
        first = self.source("chapter-01.rtf"); self.source("chapter-02.rtf")
        _, queue, _ = scan_queue(self.project)
        activate(self.project, {"пользовательская_верификация": "после-каждого-файла"}, queue)
        identifier, _, _ = self._publish(first.name)
        self.assertEqual("ожидает-одобрения", load_progress(self.project)["статус_книги"])
        approve_files(self.project, [identifier])
        self.assertEqual("в-работе", load_progress(self.project)["статус_книги"])

    def test_ambiguous_name_lists_matches(self):
        entries = [{"имя": "part-1.rtf"}, {"имя": "part-10.rtf"}]
        with self.assertRaisesRegex(ValueError, "part-1.rtf"):
            resolve_chapter("part", entries)

    def _publish(self, name="chapter-01.rtf", replace_current=False):
        source = self.source(name)
        identifier = chapter_id(source)
        item = load_progress(self.project)["файлы"].get(identifier, {})
        transaction_number = int(item.get("номер_транзакции", item.get("ревизия", 0))) + 1
        chapter = {"id": identifier, "имя": source.name, "sha256": file_sha256(source)}
        candidate = self.project / "work" / f"candidate-{identifier}-{transaction_number}.rtf"
        candidate.write_text(RTF, encoding="latin-1")
        prepared = self.project / "work" / f"prepared-state-{identifier}-{transaction_number}"
        shutil.copytree(self.project / "state", prepared)
        transaction = prepare_transaction(
            self.project, chapter, candidate, prepared, [{"status": "remarks"}], replace_current=replace_current,
        )
        return identifier, commit_transaction(self.project, transaction), transaction

    def _add_published_annotation(self, identifier, result, note_id="known-note"):
        add_annotations(result, [{"id": note_id, "точная_цитата": "First", "объяснение": "Проверить."}])
        progress = load_progress(self.project)
        item = progress["файлы"][identifier]
        item["sha256_результата"] = file_sha256(result)
        item["отпечатки_при_публикации"] = rtf_fingerprints(result)
        item["аннотации_при_публикации"] = [note_id]
        module.save_progress(self.project, progress)

    def test_publish_with_reports_and_versioned_retranslation(self):
        identifier, first, first_transaction = self._publish()
        self.assertEqual("chapter-01.ru.rtf", first.name)
        self.assertTrue((first_transaction / "verification-reports.json").is_file())
        prepared = self.project / "work" / "prepared-again"
        shutil.copytree(self.project / "state", prepared)
        source = self.project / "input" / "chapter-01.rtf"
        chapter = {"id": identifier, "имя": source.name, "sha256": file_sha256(source)}
        candidate = self.project / "work" / "candidate-2.rtf"; candidate.write_text(RTF, encoding="latin-1")
        second = commit_transaction(self.project, prepare_transaction(self.project, chapter, candidate, prepared))
        self.assertRegex(second.name, r"chapter-01\.ru\.v002\.\d{8}\.rtf")
        self.assertTrue(first.is_file())
        self.assertEqual("sample.ru.v002.20260822.rtf", output_name("sample.rtf", 2, datetime(2026, 8, 22, tzinfo=timezone.utc)))

    def test_failed_commit_restores_previous_state_and_output(self):
        identifier, first, _ = self._publish()
        state_before = (self.project / "state" / "glossary.md").read_bytes()
        prepared = self.project / "work" / "prepared-fail"; shutil.copytree(self.project / "state", prepared)
        (prepared / "glossary.md").write_text("candidate state", encoding="utf-8")
        source = self.project / "input" / "chapter-01.rtf"
        chapter = {"id": identifier, "имя": source.name, "sha256": file_sha256(source)}
        candidate = self.project / "work" / "candidate-fail.rtf"; candidate.write_text(RTF, encoding="latin-1")
        transaction = prepare_transaction(self.project, chapter, candidate, prepared)
        with mock.patch.object(module, "save_progress", side_effect=OSError("boom")):
            with self.assertRaisesRegex(ValueError, "восстановлены"):
                commit_transaction(self.project, transaction)
        self.assertEqual(state_before, (self.project / "state" / "glossary.md").read_bytes())
        self.assertTrue(first.is_file())
        self.assertFalse((self.project / "output" / output_name(source.name, 2)).exists())

        retry_state = self.project / "work" / "prepared-retry"
        shutil.copytree(self.project / "state", retry_state)
        retry_candidate = self.project / "work" / "candidate-retry.rtf"
        retry_candidate.write_text(RTF, encoding="latin-1")
        retry_transaction = prepare_transaction(self.project, chapter, retry_candidate, retry_state)
        retry_metadata = json.loads((retry_transaction / "transaction.json").read_text(encoding="utf-8"))
        self.assertEqual("003", retry_transaction.name)
        self.assertEqual(1, retry_metadata["родительская_транзакция"])
        retry_output = commit_transaction(self.project, retry_transaction)
        self.assertRegex(retry_output.name, r"chapter-01\.ru\.v002\.\d{8}\.rtf")

    def test_failed_first_state_rename_keeps_original_state(self):
        identifier, _, _ = self._publish()
        state_before = {name: (self.project / "state" / name).read_bytes() for name in STATE_FILES}
        prepared = self.project / "work" / "prepared-first-rename-failure"
        shutil.copytree(self.project / "state", prepared)
        source = self.project / "input" / "chapter-01.rtf"
        chapter = {"id": identifier, "имя": source.name, "sha256": file_sha256(source)}
        candidate = self.project / "work" / "candidate-first-rename-failure.rtf"
        candidate.write_text(RTF, encoding="latin-1")
        transaction = prepare_transaction(self.project, chapter, candidate, prepared)
        real_replace = module._replace_path

        def fail_first_rename(source_path, target_path):
            if source_path == self.project / "state":
                raise OSError("first rename failed")
            return real_replace(source_path, target_path)

        with mock.patch.object(module, "_replace_path", side_effect=fail_first_rename):
            with self.assertRaisesRegex(ValueError, "восстановлены"):
                commit_transaction(self.project, transaction)
        for name, content in state_before.items():
            self.assertEqual(content, (self.project / "state" / name).read_bytes())

    def test_publish_boundary_rechecks_forbidden_letter(self):
        source = self.source()
        identifier = chapter_id(source)
        chapter = {"id": identifier, "имя": source.name, "sha256": file_sha256(source)}
        prepared = self.project / "work" / "prepared-forbidden"
        shutil.copytree(self.project / "state", prepared)
        candidate = self.project / "work" / "candidate-clean.rtf"
        candidate.write_text(RTF, encoding="latin-1")
        transaction = prepare_transaction(self.project, chapter, candidate, prepared)
        staged = transaction / "candidate.rtf"
        staged.write_text(r"{\rtf1\ansi birch \u1105?\par}", encoding="latin-1")
        metadata_path = transaction / "transaction.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["sha256_кандидата"] = file_sha256(staged)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "запрещенную букву"):
            commit_transaction(self.project, transaction)

    def test_failed_user_revision_restores_same_published_file(self):
        identifier, result, _ = self._publish()
        add_annotations(result, [{"id": "retry-note", "точная_цитата": "First", "объяснение": "Исправить."}])
        register_feedback(self.project, identifier, ["retry-note"])
        output_before = result.read_bytes()
        state_before = (self.project / "state" / "glossary.md").read_bytes()
        prepared = self.project / "work" / "prepared-user-failure"
        shutil.copytree(self.project / "state", prepared)
        source = self.project / "input" / "chapter-01.rtf"
        chapter = {"id": identifier, "имя": source.name, "sha256": file_sha256(source)}
        candidate = self.project / "work" / "candidate-user-failure.rtf"
        candidate.write_text(r"{\rtf1\ansi Edited scene.\par}", encoding="latin-1")
        transaction = prepare_transaction(self.project, chapter, candidate, prepared, replace_current=True)
        with mock.patch.object(module, "save_progress", side_effect=OSError("boom")):
            with self.assertRaisesRegex(ValueError, "восстановлены"):
                commit_transaction(self.project, transaction)
        self.assertEqual(output_before, result.read_bytes())
        self.assertEqual(state_before, (self.project / "state" / "glossary.md").read_bytes())

    def test_multi_file_approval_validates_every_file_before_changes(self):
        first_id, first_result, _ = self._publish("chapter-01.rtf")
        second_id, second_result, _ = self._publish("chapter-02.rtf")
        self._add_published_annotation(first_id, first_result)
        add_annotations(second_result, [{"id": "fresh-note", "точная_цитата": "First", "объяснение": "Новое замечание."}])
        first_before = first_result.read_bytes()
        progress_before = (self.project / "progress.json").read_bytes()

        with self.assertRaisesRegex(ValueError, "новые замечания"):
            approve_files(self.project, [first_id, second_id])

        self.assertEqual(first_before, first_result.read_bytes())
        self.assertEqual(progress_before, (self.project / "progress.json").read_bytes())
        self.assertEqual(["known-note"], [note["id"] for note in extract_annotations(first_result)])

    def test_approval_commit_failure_restores_outputs_progress_and_active_marker(self):
        identifier, result, _ = self._publish()
        self._add_published_annotation(identifier, result)
        activate(self.project, {"пользовательская_верификация": "в-финале"}, [])
        output_before = result.read_bytes()
        progress_before = (self.project / "progress.json").read_bytes()
        active = self.project / "work" / "active.json"
        active_before = active.read_bytes()
        real_copy = module._copy_atomic

        def fail_progress_publish(source_path, target_path):
            if source_path.name == "next-progress.json" and target_path == self.project / "progress.json":
                raise OSError("progress publish failed")
            return real_copy(source_path, target_path)

        with mock.patch.object(module, "_copy_atomic", side_effect=fail_progress_publish):
            with self.assertRaisesRegex(ValueError, "изменения отменены"):
                approve_files(self.project, [identifier])

        self.assertEqual(output_before, result.read_bytes())
        self.assertEqual(progress_before, (self.project / "progress.json").read_bytes())
        self.assertEqual(active_before, active.read_bytes())

    def test_start_over_is_confirmed_and_backed_up(self):
        _, result, _ = self._publish()
        self.assertIn(self.project / "output", restart_project(self.project, confirmed=False))
        backup = restart_project(self.project, confirmed=True)
        self.assertTrue((backup / "output" / result.name).is_file())
        self.assertEqual([], list((self.project / "output").iterdir()))
        self.assertEqual("ожидает-запуска", load_progress(self.project)["статус_книги"])

    def test_windows_reparse_points_are_unsafe(self):
        with mock.patch.object(Path, "is_symlink", return_value=False), mock.patch.object(
            Path, "lstat", return_value=SimpleNamespace(st_file_attributes=0x400),
        ):
            self.assertTrue(module.is_unsafe_link(Path("junction")))

    def test_restart_rejects_nested_reparse_point_before_changes(self):
        junction = self.project / "output" / "junction"
        junction.mkdir()
        with mock.patch.object(module, "is_unsafe_link", side_effect=lambda path: path == junction):
            with self.assertRaisesRegex(ValueError, "reparse point"):
                restart_project(self.project, confirmed=True)
        self.assertTrue((self.project / "state").is_dir())

    def test_feedback_ids_are_applied_once_and_approval_is_explicit(self):
        identifier, result, _ = self._publish()
        add_annotations(result, [{"id": "note-1", "точная_цитата": "First", "объяснение": "Проверить."}])
        with self.assertRaisesRegex(ValueError, "новые замечания"):
            approve_files(self.project, [identifier])
        self.assertEqual(["note-1"], register_feedback(self.project, identifier, ["note-1"]))
        self.assertEqual([], register_feedback(self.project, identifier, ["note-1"]))
        with self.assertRaisesRegex(ValueError, "обновите текущий"):
            complete_feedback_revision(self.project, identifier)
        _, revised, _ = self._publish(replace_current=True)
        self.assertEqual(result, revised)
        add_annotations(revised, [{"id": "note-1", "точная_цитата": "First", "объяснение": "Проверить."}])
        complete_feedback_revision(self.project, identifier)
        add_annotations(revised, [{"id": "note-2", "точная_цитата": "scene", "объяснение": "Проверить снова."}])
        feedback, errors = scan_published_feedback(self.project)
        self.assertEqual([], errors)
        self.assertEqual(["note-2"], [note["id"] for note in feedback[identifier]])
        register_feedback(self.project, identifier, ["note-2"])
        _, final, _ = self._publish(replace_current=True)
        self.assertEqual(result, final)
        add_annotations(final, [
            {"id": "note-1", "точная_цитата": "First", "объяснение": "Проверить."},
            {"id": "note-2", "точная_цитата": "scene", "объяснение": "Проверить снова."},
        ])
        complete_feedback_revision(self.project, identifier)
        approve_files(self.project, [identifier])
        progress = load_progress(self.project)
        self.assertEqual("готово", progress["статус_книги"])
        self.assertEqual(1, progress["файлы"][identifier]["ревизия"])
        self.assertEqual(2, len(progress["файлы"][identifier]["циклы_пользовательской_доработки"]))
        self.assertEqual([], extract_annotations(final))

    def test_document_and_chat_feedback_share_contract(self):
        identifier, result, _ = self._publish()
        add_annotations(result, [{"id": "user-rtf", "точная_цитата": "First", "объяснение": "Сделать точнее."}])
        feedback, errors = scan_published_feedback(self.project)
        self.assertEqual([], errors)
        self.assertEqual("user-rtf", feedback[identifier][0]["id"])
        register_feedback(self.project, identifier, ["user-rtf"])
        feedback, errors = scan_published_feedback(self.project)
        self.assertEqual({}, feedback)
        issue = chat_feedback_to_issue("First", "Нужен другой регистр.", 1)
        self.assertEqual("пользовательская", issue["серьезность"])
        self.assertEqual(issue["id"], chat_feedback_to_issue("First", "Нужен другой регистр.", 1)["id"])

    def test_direct_edit_during_review_is_rejected(self):
        _, result, _ = self._publish()
        result.write_text(result.read_text(encoding="latin-1").replace("First", "Other"), encoding="latin-1")
        _, errors = scan_published_feedback(self.project)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
