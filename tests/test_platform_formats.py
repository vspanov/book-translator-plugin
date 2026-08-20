import json
import configparser
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/book-translator/scripts"
sys.path.insert(0, str(SCRIPTS))
import documents


class PlatformFormatTests(unittest.TestCase):
    def test_output_format_aliases_normalize_from_config_and_skill_default(self):
        parser = configparser.ConfigParser()
        parser.read(
            SCRIPTS.parent / "assets/config.ini",
            encoding="utf-8",
        )
        configured = parser["перевод"]["выходной_формат"]
        aliases = {
            configured: "original",
            "как-в-оригинале": "original",
            "как в оригинале": "original",
            "оригинал": "original",
            "original": "original",
            "docx": "docx",
            ".docx": "docx",
            "pages": "pages",
            ".pages": "pages",
        }
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            chapter = project / "chapter-1.docx"
            chapter.write_bytes(b"source")
            for value, expected in aliases.items():
                with self.subTest(value=value):
                    self.assertEqual(expected, documents.normalize_output_format(value))
                    self.assertEqual(
                        [], documents.check_output_conflicts(project, [chapter], value)
                    )

    def test_windows_rejects_pages_input_and_output(self):
        input_errors = documents.preflight_formats({".pages"}, "docx", "Windows", False)
        output_errors = documents.preflight_formats({".docx"}, "pages", "Windows", False)

        self.assertTrue(any("Windows" in error and "Pages" in error for error in input_errors))
        self.assertTrue(any("Windows" in error and "Pages" in error for error in output_errors))

    def test_macos_requires_installed_pages(self):
        errors = documents.preflight_formats({".pages"}, "pages", "Darwin", False)

        self.assertTrue(any("не найдено" in error for error in errors))

    def test_docx_is_supported_on_both_platforms(self):
        self.assertEqual([], documents.preflight_formats({".docx"}, "docx", "Windows", False))
        self.assertEqual([], documents.preflight_formats({".docx"}, "docx", "Darwin", False))

    def test_other_system_is_not_supported(self):
        errors = documents.preflight_formats({".docx"}, "docx", "Linux", False)

        self.assertTrue(any("не поддерживается" in error for error in errors))

    def test_pages_bridge_requires_explicit_permission_before_starting_process(self):
        with patch.object(documents.subprocess, "run") as run:
            with self.assertRaisesRegex(PermissionError, "разреш"):
                documents.run_pages_bridge("export", Path("a.pages"), Path("a.docx"), allowed=False)

        run.assert_not_called()

    def test_pages_bridge_uses_argv_and_reports_process_error(self):
        completed = documents.subprocess.CompletedProcess([], 1, "", "Pages недоступен")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pages"
            destination = root / "result.docx"
            source.mkdir()
            with patch.object(documents.subprocess, "run", return_value=completed) as run:
                with self.assertRaisesRegex(RuntimeError, "Pages не смог"):
                    documents.run_pages_bridge("export", source, destination, allowed=True)

        command = run.call_args.args[0]
        self.assertEqual("osascript", command[0])
        self.assertEqual("export", command[2])
        self.assertEqual(str(source.resolve()), command[3])
        self.assertEqual(str(destination.resolve()), command[4])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_pages_bridge_rejects_missing_source_before_starting_process(self):
        with patch.object(documents.subprocess, "run") as run:
            with self.assertRaisesRegex(FileNotFoundError, "не найден"):
                documents.run_pages_bridge("export", Path("missing.pages"), Path("result.docx"), allowed=True)

        run.assert_not_called()

    def test_pages_bridge_rejects_wrong_suffix_before_starting_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = {
                "исходник": (root / "source.docx", root / "result.docx"),
                "результат": (root / "source.pages", root / "result.pages"),
            }
            for name, (source, destination) in cases.items():
                with self.subTest(name=name):
                    if source.suffix == ".pages":
                        source.mkdir()
                    else:
                        source.write_bytes(b"docx")
                    with patch.object(documents.subprocess, "run") as run:
                        with self.assertRaisesRegex(ValueError, "формат"):
                            documents.run_pages_bridge("export", source, destination, allowed=True)

                    run.assert_not_called()

    def test_pages_bridge_rejects_existing_destination_before_starting_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pages"
            destination = root / "result.docx"
            source.mkdir()
            destination.write_bytes(b"existing")
            with patch.object(documents.subprocess, "run") as run:
                with self.assertRaisesRegex(FileExistsError, "уже существует"):
                    documents.run_pages_bridge("export", source, destination, allowed=True)

        run.assert_not_called()

    def test_pages_bridge_rejects_missing_or_empty_successful_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pages"
            source.mkdir()
            for name, create_result in {
                "отсутствующий": lambda path: None,
                "пустой_файл": lambda path: path.touch(),
                "пустая_папка": lambda path: path.mkdir(),
            }.items():
                with self.subTest(name=name):
                    destination = root / f"{name}.docx"
                    def complete(command, **_):
                        create_result(destination)
                        return documents.subprocess.CompletedProcess(command, 0, "", "")

                    with patch.object(documents.subprocess, "run", side_effect=complete):
                        with self.assertRaisesRegex(RuntimeError, "не создан|пуст"):
                            documents.run_pages_bridge("export", source, destination, allowed=True)

    def test_pages_bridge_accepts_nonempty_pages_package_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.DOCX"
            destination = root / "result.PAGES"
            source.write_bytes(b"docx")

            def complete(command, **_):
                destination.mkdir()
                (destination / "index.xml").write_text("содержимое", encoding="utf-8")
                return documents.subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(documents.subprocess, "run", side_effect=complete):
                documents.run_pages_bridge("import", source, destination, allowed=True)

            self.assertTrue(destination.is_dir())
            self.assertTrue(any(destination.iterdir()))

    def test_pages_bridge_rejects_unknown_mode_before_starting_process(self):
        with patch.object(documents.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "Направление"):
                documents.run_pages_bridge("convert", Path("a"), Path("b"), allowed=True)

        run.assert_not_called()

    def test_missing_python_docx_stops_with_install_instruction(self):
        errors = documents.preflight_dependency(None)

        self.assertTrue(any("python-docx" in error and "разреш" in error for error in errors))

    def test_python_docx_requires_supported_major_and_minor_version(self):
        self.assertEqual([], documents.preflight_dependency("1.2.0"))
        self.assertTrue(documents.preflight_dependency("1.1.9"))
        self.assertTrue(documents.preflight_dependency("2.0"))

    def test_old_python_is_rejected(self):
        errors = documents.preflight_python((3, 9, 18))

        self.assertTrue(any("3.10" in error for error in errors))

    def test_runtime_report_only_returns_runtime_details(self):
        report = documents.runtime_report()

        self.assertEqual({"python", "python_version", "python_docx"}, set(report))
        self.assertEqual(sys.executable, report["python"])

    def test_preflight_and_report_work_when_python_docx_cannot_be_imported(self):
        code = f'''\
import importlib.abc
import importlib.metadata
import json
import sys

class BlockDocx(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "docx" or fullname.startswith("docx."):
            raise ModuleNotFoundError("python-docx недоступен")
        return None

def missing_package(name):
    raise importlib.metadata.PackageNotFoundError(name)

sys.meta_path.insert(0, BlockDocx())
importlib.metadata.version = missing_package
sys.path.insert(0, {str(SCRIPTS)!r})
import documents
print(json.dumps({{
    "formats": documents.preflight_formats({{".docx"}}, "docx", "Windows", False),
    "dependency": documents.preflight_dependency(None),
    "report": documents.runtime_report(),
}}))
'''
        completed = documents.subprocess.run(
            [sys.executable, "-c", code], text=True, capture_output=True, check=False
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual([], report["formats"])
        self.assertTrue(report["dependency"])
        self.assertIsNone(report["report"]["python_docx"])

    @unittest.skipUnless(sys.platform == "darwin", "Проверка Pages выполняется только на macOS")
    @unittest.skipUnless(
        os.environ.get("BOOK_TRANSLATOR_TEST_PAGES") == "1",
        "Запуск Pages требует явного BOOK_TRANSLATOR_TEST_PAGES=1",
    )
    def test_real_pages_round_trip(self):
        fixture = Path(__file__).resolve().parent / "fixtures/pages-smoke.pages"
        self.assertTrue(fixture.is_dir(), "Нет проверенной фикстуры Pages для реального smoke-теста.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = root / "exported.docx"
            imported = root / "imported.pages"
            documents.run_pages_bridge("export", fixture, exported, allowed=True)
            documents.run_pages_bridge("import", exported, imported, allowed=True)
            self.assertGreater(exported.stat().st_size, 0)
            self.assertGreater(imported.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
