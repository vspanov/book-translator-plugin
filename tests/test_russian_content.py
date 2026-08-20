import json
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
FORBIDDEN_BOOK_TERMS = re.compile(r"Илиш|Elish|Magnus|Джейд", re.IGNORECASE)


class RussianContentTests(unittest.TestCase):
    def test_human_facing_markdown_and_agents_are_russian(self):
        """Ловит англоязычную пользовательскую инструкцию или описание роли."""
        files = [ROOT / "README.md", ROOT / "skills" / "book-translator" / "SKILL.md"]
        files += list((ROOT / "skills" / "book-translator" / "assets").glob("*.md"))
        files += list((ROOT / "skills" / "book-translator" / "references").glob("*.md"))
        for path in files:
            with self.subTest(path=path):
                self.assertRegex(path.read_text(encoding="utf-8"), CYRILLIC)
        for path in (ROOT / "agents").glob("*.toml"):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            with self.subTest(path=path, field="description"):
                self.assertRegex(data["description"], CYRILLIC)
            with self.subTest(path=path, field="developer_instructions"):
                self.assertRegex(data["developer_instructions"], CYRILLIC)

    def test_plugin_and_hook_human_fields_are_russian(self):
        """Ловит нерусские описания манифеста и сообщения hook."""
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertRegex(manifest["description"], CYRILLIC)
        self.assertRegex(hooks["description"], CYRILLIC)
        stop_hook = hooks["hooks"]["Stop"][0]["hooks"][0]
        self.assertRegex(stop_hook["statusMessage"], CYRILLIC)

    def test_universal_instructions_do_not_contain_old_book_names(self):
        """Ловит перенос частных имён старой книги в универсальные инструкции."""
        files = [ROOT / "skills" / "book-translator" / "SKILL.md"]
        files += list((ROOT / "skills" / "book-translator" / "references").glob("*.md"))
        files += list((ROOT / "agents").glob("*.toml"))
        for path in files:
            with self.subTest(path=path):
                self.assertNotRegex(path.read_text(encoding="utf-8"), FORBIDDEN_BOOK_TERMS)


if __name__ == "__main__":
    unittest.main()
