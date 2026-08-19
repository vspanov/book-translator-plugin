import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/book-translator/scripts"
sys.path.insert(0, str(SCRIPTS))
import progress


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


if __name__ == "__main__":
    unittest.main()
