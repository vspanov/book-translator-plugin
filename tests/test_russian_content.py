import configparser
import json
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
LETTERS = re.compile(r"[А-Яа-яЁёA-Za-z]")
FORBIDDEN_BOOK_TERMS = re.compile(
    r"илиш|elish|магнус|magnus|дрейк|drake|кел|kel|джейд|jade|силас|silas|coke",
    re.IGNORECASE,
)
TECHNICAL_IDENTIFIERS = re.compile(
    r"\b(?:docx|pages|windows|powershell|posix|custom\s+agents?|custom\s+agent|translator|verifier|editor|state-updater|book_translator_[a-z_]+)\b",
    re.IGNORECASE,
)


def human_lines(text: str) -> list[str]:
    lines = []
    fenced = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = re.sub(r"`[^`]*`", "", line).strip()
        if line.casefold().startswith("custom agent:"):
            continue
        line = TECHNICAL_IDENTIFIERS.sub("", line)
        if not line or line == "---" or line.startswith("name:"):
            continue
        if not LETTERS.search(line):
            continue
        lines.append(line)
    return lines


def assert_russian_human_text(test: unittest.TestCase, text: str) -> None:
    lines = human_lines(text)
    test.assertTrue(lines)
    for line in lines:
        letters = LETTERS.findall(line)
        cyrillic = CYRILLIC.findall(line)
        with test.subTest(line=line):
            test.assertTrue(cyrillic)
            test.assertGreaterEqual(len(cyrillic) / len(letters), 0.55)


class RussianContentTests(unittest.TestCase):
    def test_human_facing_content_has_russian_lines(self):
        """Ловит строку инструкции, где русского текста меньше большинства."""
        files = [ROOT / "README.md", ROOT / "skills" / "book-translator" / "SKILL.md"]
        files += list((ROOT / "skills" / "book-translator" / "assets").glob("*.md"))
        files += list((ROOT / "skills" / "book-translator" / "references").glob("*.md"))
        for path in files:
            with self.subTest(path=path):
                assert_russian_human_text(self, path.read_text(encoding="utf-8"))
        config = configparser.ConfigParser()
        config.read(ROOT / "skills" / "book-translator" / "assets" / "config.ini", encoding="utf-8")
        for section in config.values():
            for value in section.values():
                with self.subTest(value=value):
                    assert_russian_human_text(self, value)
        for path in (ROOT / "agents").glob("*.toml"):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            with self.subTest(path=path, field="description"):
                assert_russian_human_text(self, data["description"])
            with self.subTest(path=path, field="developer_instructions"):
                assert_russian_human_text(self, data["developer_instructions"])

    def test_plugin_and_hook_human_fields_are_russian(self):
        """Ловит нерусские описания манифеста и сообщения hook."""
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        assert_russian_human_text(self, manifest["description"])
        assert_russian_human_text(self, hooks["description"])
        stop_hook = hooks["hooks"]["Stop"][0]["hooks"][0]
        assert_russian_human_text(self, stop_hook["statusMessage"])

    def test_readme_offers_copyable_platform_commands_and_installer_runs(self):
        """Ловит README без вызываемых команд PowerShell и POSIX либо сломанный установщик."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("### Команды для Windows (PowerShell)", readme)
        self.assertIn('& "<путь-к-python>"', readme)
        self.assertIn("### Команды для POSIX-систем", readme)
        self.assertIn('"<путь-к-python>" "<абсолютный-путь-к-плагину>/skills/book-translator/scripts/install-agents.py"', readme)
        installer = ROOT / "skills" / "book-translator" / "scripts" / "install-agents.py"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agents"
            preview = subprocess.run(
                [sys.executable, str(installer), "--source", str(ROOT / "agents"), "--target", str(target), "--plan"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, preview.returncode, preview.stderr)
            self.assertFalse(target.exists())
            confirmed = subprocess.run(
                [sys.executable, str(installer), "--source", str(ROOT / "agents"), "--target", str(target), "--confirm"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, confirmed.returncode, confirmed.stderr)
            self.assertEqual(4, len(list(target.glob("*.toml"))))

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
