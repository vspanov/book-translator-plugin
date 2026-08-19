import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/book-translator/scripts"
sys.path.insert(0, str(SCRIPTS))
import documents


class ChapterDiscoveryTests(unittest.TestCase):
    def test_natural_order_places_2_before_10(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            for name in ("chapter-10.docx", "chapter-2.docx", "chapter-1.docx"):
                (project / name).write_bytes(b"docx")
            names = [path.name for path in documents.discover_chapters(project)]
            self.assertEqual(["chapter-1.docx", "chapter-2.docx", "chapter-10.docx"], names)

    def test_input_and_root_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "input").mkdir()
            (project / "input/chapter-1.docx").write_bytes(b"docx")
            (project / "chapter-2.docx").write_bytes(b"docx")
            with self.assertRaisesRegex(ValueError, "одновременно"):
                documents.discover_chapters(project)

    def test_duplicate_casefolded_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "Chapter-K.docx").write_bytes(b"one")
            (project / "chapter-k.docx").write_bytes(b"two")
            with self.assertRaisesRegex(ValueError, "неоднознач"):
                documents.discover_chapters(project)

    def test_manifest_uses_portable_paths_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            chapter = project / "input/chapter-1.docx"
            chapter.parent.mkdir()
            chapter.write_bytes(b"first")

            manifest = documents.build_manifest(project, [chapter])

            self.assertEqual(1, manifest["версия"])
            self.assertEqual("input", manifest["источник"])
            self.assertEqual(
                {
                    "номер": 1,
                    "имя": "chapter-1.docx",
                    "путь": "input/chapter-1.docx",
                    "sha256": "a7937b64b8caa58f03721bb6bacf5c78cb235febe0e70b1b84cd99541461a08e",
                },
                manifest["главы"][0],
            )
            saved = json.loads((project / "work/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest, saved)

    def test_changed_completed_source_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            chapter = project / "chapter-1.docx"
            chapter.write_bytes(b"first")
            manifest = documents.build_manifest(project, [chapter])
            chapter.write_bytes(b"changed")
            errors = documents.verify_manifest(project, manifest)
            self.assertTrue(any("изменена" in error for error in errors))

    def test_new_chapter_is_allowed_only_after_existing_order(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            first = project / "chapter-2.docx"
            first.write_bytes(b"first")
            manifest = documents.build_manifest(project, [first])
            inserted = project / "chapter-1.docx"
            inserted.write_bytes(b"inserted")
            self.assertTrue(any("после последней" in error
                                for error in documents.verify_manifest(project, manifest)))

    def test_appended_chapter_has_no_manifest_error(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            first = project / "chapter-1.docx"
            first.write_bytes(b"first")
            manifest = documents.build_manifest(project, [first])
            (project / "chapter-2.docx").write_bytes(b"second")
            self.assertEqual([], documents.verify_manifest(project, manifest))

    def test_output_name_collision_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            first = project / "chapter-1.docx"
            second = project / "chapter-1.pages"
            first.write_bytes(b"docx")
            second.write_bytes(b"pages")
            errors = documents.check_output_conflicts(project, [first, second], "docx")
            self.assertTrue(any("одно имя" in error for error in errors))

    def test_existing_output_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            chapter = project / "chapter-1.docx"
            chapter.write_bytes(b"source")
            output = project / "output"
            output.mkdir()
            (output / "chapter-1.docx").write_bytes(b"result")
            errors = documents.check_output_conflicts(project, [chapter], "docx")
            self.assertTrue(any("уже существует" in error for error in errors))

    def test_confirmed_output_before_checkpoint_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            chapter = project / "chapter-1.docx"
            chapter.write_bytes(b"source")
            output = project / "output"
            output.mkdir()
            result = output / "chapter-1.docx"
            result.write_bytes(b"published")
            (project / "progress.json").write_text(
                json.dumps(
                    {
                        "версия": 1,
                        "последняя_готовая_глава": "chapter-1.docx",
                        "sha256_результата": documents.file_sha256(result),
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual([], documents.check_output_conflicts(project, [chapter], "docx"))

    def test_deleted_chapter_and_appended_chapter_report_only_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            first = project / "chapter-1.docx"
            second = project / "chapter-2.docx"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            manifest = documents.build_manifest(project, [first, second])
            first.unlink()
            (project / "chapter-3.docx").write_bytes(b"third")

            errors = documents.verify_manifest(project, manifest)

            self.assertTrue(any("удалена" in error for error in errors))
            self.assertFalse(any("после последней" in error for error in errors))

    def test_case_changed_manifest_path_is_reported_as_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            original = project / "Chapter-K.docx"
            original.write_bytes(b"same")
            manifest = documents.build_manifest(project, [original])
            original.unlink()
            renamed = project / "chapter-k.docx"
            renamed.write_bytes(b"same")

            errors = documents.verify_manifest(project, manifest)

            self.assertTrue(any("удалена" in error for error in errors))

    def test_deleted_last_chapter_and_inserted_earlier_chapter_report_both_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            first = project / "chapter-1.docx"
            last = project / "chapter-2.docx"
            first.write_bytes(b"first")
            last.write_bytes(b"last")
            manifest = documents.build_manifest(project, [first, last])
            last.unlink()
            (project / "chapter-1.5.docx").write_bytes(b"inserted")

            errors = documents.verify_manifest(project, manifest)

            self.assertTrue(any("удалена" in error for error in errors))
            self.assertTrue(any("после последней" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
