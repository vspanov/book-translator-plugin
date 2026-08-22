from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "book-translator" / "scripts"))
from documents import add_annotations, chapter_id, extract_annotations, file_sha256
from progress import (
    activate, approve_files, commit_transaction, initialize_project, load_progress,
    prepare_state, prepare_transaction, register_feedback, replace_managed_contribution,
    scan_published_feedback, scan_queue,
)


class EndToEndTests(unittest.TestCase):
    def test_book_feedback_new_file_and_targeted_retranslation(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "book"
            initialize_project(project)
            first = project / "input" / "chapter-01.rtf"
            second = project / "input" / "chapter-02.rtf"
            first.write_text(r"{\rtf1\ansi First source.\par}", encoding="latin-1")
            second.write_text(r"{\rtf1\ansi Second source.\par}", encoding="latin-1")
            _, queue, errors = scan_queue(project)
            self.assertEqual([], errors)
            activate(project, {"пользовательская_верификация": "в-финале"}, queue)

            def publish(source: Path, text: str, contribution: str, issue_id: str | None = None, preserve_from: Path | None = None):
                identifier = chapter_id(source)
                progress = load_progress(project)
                revision = int(progress["файлы"].get(identifier, {}).get("ревизия", 0)) + 1
                prepared = project / "work" / f"prepared-{identifier}-{revision}"
                prepare_state(project, identifier, prepared)
                replace_managed_contribution(prepared, identifier, {"glossary.md": contribution})
                candidate = project / "work" / f"candidate-{identifier}-{revision}.rtf"
                if preserve_from is None:
                    candidate.write_text(r"{\rtf1\ansi " + text + r"\par}", encoding="latin-1")
                else:
                    candidate.write_text(preserve_from.read_text(encoding="latin-1").replace(" translation.", " revision."), encoding="latin-1")
                reports = []
                if issue_id:
                    issue = {"id": issue_id, "точная_цитата": text.split()[0], "объяснение": "Проверить оттенок."}
                    add_annotations(candidate, [issue]); reports = [{"замечания": [issue]}]
                chapter = {"id": identifier, "имя": source.name, "sha256": file_sha256(source)}
                return identifier, commit_transaction(project, prepare_transaction(project, chapter, candidate, prepared, reports))

            first_id, first_output = publish(first, "First translation.", "Термин первой сцены.", "agent-1")
            second_id, second_output = publish(second, "Second translation.", "Термин второй сцены.")
            self.assertEqual("ожидает-одобрения", load_progress(project)["статус_книги"])
            second_hash = file_sha256(second_output)

            add_annotations(first_output, [{"id": "user-1", "точная_цитата": "First", "объяснение": "Изменить интонацию."}])
            feedback, errors = scan_published_feedback(project)
            self.assertEqual([], errors)
            self.assertEqual(["user-1"], [item["id"] for item in feedback[first_id]])
            register_feedback(project, first_id, ["user-1"])
            first_id, revised = publish(first, "First revision.", "Уточненный термин первой сцены.", preserve_from=first_output)
            self.assertRegex(revised.name, r"\.v002\.\d{8}\.rtf$")
            self.assertEqual({"agent-1", "user-1"}, {item["id"] for item in extract_annotations(revised)})
            self.assertEqual(second_hash, file_sha256(second_output))
            approve_files(project, [first_id, second_id])
            self.assertEqual([], extract_annotations(revised))

            inserted = project / "input" / "chapter-00.rtf"
            inserted.write_text(r"{\rtf1\ansi Inserted source.\par}", encoding="latin-1")
            _, queue, errors = scan_queue(project)
            self.assertEqual([inserted.name], [item["имя"] for item in queue])
            self.assertEqual([], errors)
            activate(project, {"пользовательская_верификация": "в-финале"}, queue)
            inserted_id, _ = publish(inserted, "Inserted translation.", "Термин вставленной сцены.")
            approve_files(project, [inserted_id])

            user_state = project / "state" / "decisions.md"
            user_state.write_text(user_state.read_text(encoding="utf-8").replace("<!-- user:end -->", "Новое решение пользователя.\n<!-- user:end -->"), encoding="utf-8")
            before_second_state = (project / "state" / "glossary.md").read_text(encoding="utf-8")
            activate(project, {"пользовательская_верификация": "в-финале"}, [{"id": first_id}])
            _, third = publish(first, "Fresh translation.", "Новый вклад первой сцены.")
            self.assertRegex(third.name, r"\.v003\.\d{8}\.rtf$")
            self.assertEqual(second_hash, file_sha256(second_output))
            after_state = (project / "state" / "glossary.md").read_text(encoding="utf-8")
            self.assertIn("Термин второй сцены.", before_second_state)
            self.assertIn("Термин второй сцены.", after_state)
            self.assertIn("Новое решение пользователя.", user_state.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
