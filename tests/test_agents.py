import importlib.util
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "book-translator" / "scripts" / "install-agents.py"
EXPECTED = {
    "translator.toml": "book_translator_translator",
    "verifier.toml": "book_translator_verifier",
    "editor.toml": "book_translator_editor",
    "state-updater.toml": "book_translator_state_updater",
}


def load_installer():
    spec = importlib.util.spec_from_file_location("install_agents", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AgentDefinitionTests(unittest.TestCase):
    def test_four_narrow_agents_have_russian_instructions_and_no_model_pin(self):
        self.assertEqual(set(EXPECTED), {path.name for path in (ROOT / "agents").glob("*.toml")})
        for filename, name in EXPECTED.items():
            with self.subTest(filename=filename):
                data = tomllib.loads((ROOT / "agents" / filename).read_text(encoding="utf-8"))
                self.assertEqual(name, data["name"])
                self.assertEqual("workspace-write", data["sandbox_mode"])
                self.assertGreater(len(data["developer_instructions"]), 300)
                self.assertNotIn("model", data)
                self.assertNotIn("model_reasoning_effort", data)
                self.assertIn("рус", (data["description"] + data["developer_instructions"]).lower())

    def test_editor_limits_changes_to_verified_findings(self):
        instructions = tomllib.loads((ROOT / "agents" / "editor.toml").read_text(encoding="utf-8"))["developer_instructions"].lower()
        self.assertIn("только блоки", instructions)
        self.assertIn("из отчёта verifier", instructions)
        self.assertIn("не изменяй остальные блоки", instructions)
        self.assertIn("не проводи свободную повторную редактуру", instructions)


class AgentInstallationTests(unittest.TestCase):
    def setUp(self):
        self.installer = load_installer()
        self.source = ROOT / "agents"

    def test_plan_reports_create_without_creating_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agents"
            plan = self.installer.plan_install(self.source, target)
            self.assertEqual(set(EXPECTED), {item["name"] for item in plan})
            self.assertEqual({"создать"}, {item["status"] for item in plan})
            self.assertFalse(target.exists())

    def test_cli_without_confirmation_only_prints_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agents"
            command = [sys.executable, str(SCRIPT), "--source", str(self.source), "--target", str(target), "--plan"]
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertIn("создать", completed.stdout)
            self.assertFalse(target.exists())

    def test_confirm_creates_agents_and_second_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agents"
            created = self.installer.install_agents(self.source, target, confirmed=True, overwrite=set())
            self.assertEqual(set(EXPECTED), set(created))
            self.assertEqual([], self.installer.install_agents(self.source, target, confirmed=True, overwrite=set()))
            self.assertEqual({"совпадает"}, {item["status"] for item in self.installer.plan_install(self.source, target)})

    def test_installer_requires_explicit_overwrite_and_preserves_modified_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agents"
            target.mkdir()
            existing = target / "translator.toml"
            existing.write_text("изменено пользователем", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "перезапис"):
                self.installer.install_agents(self.source, target, confirmed=True, overwrite=set())
            self.assertEqual("изменено пользователем", existing.read_text(encoding="utf-8"))

    def test_unconfirmed_install_preserves_modified_target_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agents"
            target.mkdir()
            existing = target / "translator.toml"
            existing.write_text("изменено пользователем", encoding="utf-8")
            self.assertEqual([], self.installer.install_agents(self.source, target, confirmed=False, overwrite=set()))
            self.assertEqual("изменено пользователем", existing.read_text(encoding="utf-8"))

    def test_explicit_overwrite_replaces_only_named_known_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agents"
            target.mkdir()
            existing = target / "translator.toml"
            existing.write_text("изменено пользователем", encoding="utf-8")
            replaced = self.installer.install_agents(
                self.source, target, confirmed=True, overwrite={"translator.toml"}
            )
            self.assertIn("translator.toml", replaced)
            self.assertEqual((self.source / "translator.toml").read_text(encoding="utf-8"), existing.read_text(encoding="utf-8"))

    def test_rejects_unknown_overwrite_and_unexpected_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agents"
            with self.assertRaisesRegex(ValueError, "неизвест"):
                self.installer.install_agents(self.source, target, confirmed=True, overwrite={"../../other.toml"})

            source = Path(directory) / "source"
            source.mkdir()
            for filename in EXPECTED:
                (source / filename).write_text("name = 'тест'", encoding="utf-8")
            (source / "лишний.toml").write_text("name = 'тест'", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "(?i)состав"):
                self.installer.plan_install(source, target)

    def test_rejects_target_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "переход"):
                self.installer.plan_install(self.source, root / "agents" / ".." / "outside")

    def test_rejects_symlinked_target_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "agents"
            target.mkdir()
            outside = root / "outside.toml"
            outside.write_text("изменено пользователем", encoding="utf-8")
            try:
                (target / "translator.toml").symlink_to(outside)
            except OSError as error:
                self.skipTest(f"Создание символьной ссылки недоступно: {error}")
            with self.assertRaisesRegex(ValueError, "ссыл"):
                self.installer.plan_install(self.source, target)
            self.assertEqual("изменено пользователем", outside.read_text(encoding="utf-8"))

    @unittest.skipUnless(sys.platform == "win32", "Точка повторной обработки доступна только в Windows")
    def test_rejects_reparse_target_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_target = root / "настоящий"
            real_target.mkdir()
            target = root / "agents"
            completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(real_target)], capture_output=True)
            if completed.returncode:
                self.skipTest("Создание точки повторной обработки недоступно.")
            with self.assertRaisesRegex(ValueError, "повторной"):
                self.installer.plan_install(self.source, target)


if __name__ == "__main__":
    unittest.main()
