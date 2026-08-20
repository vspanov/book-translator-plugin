import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PluginStructureTests(unittest.TestCase):
    def test_manifest_declares_skill_and_hook(self):
        manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("book-translator-plugin", manifest["name"])
        self.assertEqual("0.1.0", manifest["version"])
        self.assertEqual("./skills/", manifest["skills"])
        self.assertEqual("./hooks/hooks.json", manifest["hooks"])
        self.assertIn("перевод", manifest["description"].lower())

    def test_dependency_list_contains_only_python_docx(self):
        lines = [
            line.strip()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertEqual(["python-docx>=1.2,<2"], lines)

    def test_repository_marketplace_exposes_root_plugin(self):
        marketplace = json.loads(
            (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        )

        self.assertEqual("booksru-plugins", marketplace["name"])
        self.assertEqual("Плагины BooksRu", marketplace["interface"]["displayName"])
        self.assertEqual(1, len(marketplace["plugins"]))

        plugin = marketplace["plugins"][0]
        self.assertEqual("book-translator-plugin", plugin["name"])
        self.assertEqual(
            {
                "source": "url",
                "url": "https://github.com/vspanov/book-translator-plugin.git",
                "ref": "main",
            },
            plugin["source"],
        )
        self.assertEqual(
            {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            plugin["policy"],
        )
        self.assertEqual("Productivity", plugin["category"])


if __name__ == "__main__":
    unittest.main()
