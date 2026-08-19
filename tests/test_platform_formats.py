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
            with patch.object(documents.subprocess, "run", return_value=completed) as run:
                with self.assertRaisesRegex(RuntimeError, "Pages не смог"):
                    documents.run_pages_bridge("export", source, destination, allowed=True)

        command = run.call_args.args[0]
        self.assertEqual("osascript", command[0])
        self.assertEqual("export", command[2])
        self.assertEqual(str(source.resolve()), command[3])
        self.assertEqual(str(destination.resolve()), command[4])
        self.assertNotIn("shell", run.call_args.kwargs)

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
