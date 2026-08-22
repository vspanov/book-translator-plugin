from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "book-translator" / "scripts"))
import progress as module
from documents import add_annotations, build_manifest, chapter_id, chat_feedback_to_issue, extract_annotations, file_sha256, refresh_manifest
from progress import (
    STATE_FILES, activate, approve_files, commit_transaction, initialize_project, load_config,
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
        _, _, conflicts = refresh_manifest(self.project, refreshed)
        self.assertTrue(conflicts)
        _, queue, conflicts = refresh_manifest(self.project, refreshed, allow_changed=later.name)
        self.assertIn(later.name, [item["имя"] for item in queue])
        self.assertEqual([], conflicts)

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

    def _publish(self, name="chapter-01.rtf"):
        source = self.source(name)
        identifier = chapter_id(source)
        chapter = {"id": identifier, "имя": source.name, "sha256": file_sha256(source)}
        candidate = self.project / "work" / "candidate.rtf"
        candidate.write_text(RTF, encoding="latin-1")
        prepared = self.project / "work" / "prepared-state"
        shutil.copytree(self.project / "state", prepared)
        transaction = prepare_transaction(self.project, chapter, candidate, prepared, [{"status": "remarks"}])
        return identifier, commit_transaction(self.project, transaction), transaction

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

    def test_start_over_is_confirmed_and_backed_up(self):
        _, result, _ = self._publish()
        self.assertIn(self.project / "output", restart_project(self.project, confirmed=False))
        backup = restart_project(self.project, confirmed=True)
        self.assertTrue((backup / "output" / result.name).is_file())
        self.assertEqual([], list((self.project / "output").iterdir()))
        self.assertEqual("ожидает-запуска", load_progress(self.project)["статус_книги"])

    def test_feedback_ids_are_applied_once_and_approval_is_explicit(self):
        identifier, result, _ = self._publish()
        add_annotations(result, [{"id": "visible", "точная_цитата": "First", "объяснение": "Проверить."}])
        progress = load_progress(self.project)
        progress["файлы"][identifier]["sha256_результата"] = file_sha256(result)
        module.save_progress(self.project, progress)
        self.assertEqual(["note-1"], register_feedback(self.project, identifier, ["note-1"]))
        self.assertEqual([], register_feedback(self.project, identifier, ["note-1"]))
        approve_files(self.project, [identifier])
        self.assertEqual("готово", load_progress(self.project)["статус_книги"])
        self.assertTrue(result.is_file())
        self.assertEqual([], extract_annotations(result))

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
