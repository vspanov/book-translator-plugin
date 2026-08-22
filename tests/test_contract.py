from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_plugin_version_and_both_skills(self):
        plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("0.2.0", plugin["version"])
        self.assertTrue((ROOT / "skills" / "book-translator" / "SKILL.md").is_file())
        self.assertTrue((ROOT / "skills" / "book-translator-init" / "SKILL.md").is_file())

    def test_agents_are_assets_only_and_include_safety_and_state_rules(self):
        self.assertFalse((ROOT / "agents").exists() and list((ROOT / "agents").glob("*.toml")))
        agents = ROOT / "skills" / "book-translator" / "assets" / "agents"
        self.assertEqual(4, len(list(agents.glob("*.toml"))))
        combined = "\n".join(path.read_text(encoding="utf-8") for path in agents.glob("*.toml"))
        for phrase in ("не цензур", "пользователь", "state"):
            self.assertIn(phrase, combined.casefold())

    def test_no_external_runtime_dependencies(self):
        self.assertFalse((ROOT / "requirements.txt").exists())
        documents = (ROOT / "skills" / "book-translator" / "scripts" / "documents.py").read_text(encoding="utf-8")
        self.assertEqual({".rtf"}, __import__("ast").literal_eval(__import__("re").search(r"SUPPORTED_SUFFIXES = (\{.*?\})", documents).group(1)))

    def test_old_fixture_denylist_is_hash_only(self):
        # Plaintext values intentionally never enter the repository.
        denylist = {
            "2655064bb900fa1691b3881ed319659759ea24f953bffb8b2ce51d7fd0040643",
            "3989b04a981c92e61842de49d27c86c9b11f05a216bcf9d4d075c18be810b813",
            "e0fab04a8370296549f3958513c31d3fb5238bf924481990d01c7a9c1013563d",
            "a6b78c3080f6587975d33fcd8eba2336896589dd379848f2228f4d067cc43e5a",
            "3c8add716f15080a23d847632edb9174da9be87d270b9a6c507f6138be08b9c8",
            "54444aa7bc07ccec734fddb6f35b9918f914123ad2a0a6e5223d2cda95568de5",
            "3d4357a3f20a1dc8a843af3a8c98860c543b7de278a5d65a0a1030cf71fdc927",
            "e778226c3a53bba4e214ab009a59e96e9a27514909edd1ed2bcd6dcf23b1d66f",
            "e344ef3433595a8b7c19bcfe0a67a63832999120e44566ed721c76ddb6a14328",
            "5db6810e7cf9ff073c275265cfed5712b136fee26d7281715196e55f36f00279",
            "9bd509917b9ae7d143d7e1aef314952236a4d60bac27422d386f9dc930ea494b",
            "db9763c0e62cb499563498c1d1fee91b1511499556d1eb9741a6138d2d8edf4a",
        }
        for path in ROOT.rglob("*"):
            if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts:
                try: words = path.read_text(encoding="utf-8").casefold().split()
                except (UnicodeDecodeError, OSError): continue
                self.assertTrue(denylist.isdisjoint(hashlib.sha256(word.strip(".,:;!?—«»").encode()).hexdigest() for word in words))

    def test_rules_cover_required_quality_and_add_to_chat(self):
        principles = (ROOT / "skills" / "book-translator" / "references" / "translation-principles.md").read_text(encoding="utf-8").casefold()
        skill = (ROOT / "skills" / "book-translator" / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ("кальк", "голос", "неоднознач", "повтор", "ритм", "метафор", "не цензур", "прямую речь", "не добавляй", "буквы `ё`"):
            self.assertIn(phrase, principles)
        self.assertIn("Add to chat", skill)
        self.assertIn("кликабельную абсолютную ссылку", skill)


if __name__ == "__main__":
    unittest.main()
