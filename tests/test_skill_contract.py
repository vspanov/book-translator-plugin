import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/book-translator/SKILL.md"
REFERENCES = ROOT / "skills/book-translator/references"


class SkillContractTests(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")
        self.lower = self.text.casefold()

    def test_russian_trigger_and_chat_defaults(self):
        frontmatter = self.text.split("---", 2)[1]
        self.assertIn("name: book-translator", frontmatter)
        description = re.search(r"(?m)^description:\s*(.+)$", frontmatter).group(1)
        self.assertTrue(description.startswith("Используется, когда"))
        self.assertRegex(description.casefold(), r"английск.*книг|английск.*глав")
        self.assertIn("русск", description.casefold())
        self.assertIn("docx", description.casefold())
        self.assertIn("pages", description.casefold())
        self.assertRegex(description.casefold(), r"продолж|перезапуск|заново")
        self.assertNotRegex(description.casefold(), r"последовательно|провер|агент|этап|сначала|затем")
        self.assertIn("$book-translator [ПАПКА] [формат: pages|docx|как-в-оригинале] [продолжить|начать-заново]", self.text)
        invocation = self.text.split("Обычный вызов из чата:", 1)[1].lstrip().splitlines()[0]
        self.assertTrue(invocation.startswith("`$book-translator "))
        self.assertIn("текущая папка", self.lower)
        self.assertIn("как в оригинале", self.lower)
        self.assertIn("продолжить", self.lower)

    def test_preflight_is_complete_and_non_destructive(self):
        for phrase in (
            "операционную систему",
            "pages",
            "python 3.10",
            "python-docx",
            "чтение исходников",
            "запись в служебные каталоги",
            "порядок глав",
            "манифест",
            "конфликты выходных имён",
            "четырёх custom agents",
            "не устанавливай",
            "явного разрешения",
            "предпросмотр",
            "подтверждение",
            "обработанный исходник изменён",
        ):
            self.assertIn(phrase, self.lower)

    def test_agent_installer_uses_absolute_preview_and_confirm_commands(self):
        preview = (
            '"{python_executable}" '
            '"{plugin_root}/skills/book-translator/scripts/install-agents.py" '
            '--source "{plugin_root}/agents" --target "{codex_home}/agents" --plan'
        )
        confirm = preview.removesuffix(" --plan") + " --confirm"
        self.assertIn(preview, self.text)
        self.assertIn(confirm, self.text)
        self.assertIn("сначала вычисли абсолютные пути", self.lower)
        self.assertIn("отдельное подтверждение", self.lower)
        self.assertIn('--overwrite "{точное_имя}.toml"', self.text)

    def test_spawn_requires_real_custom_agent_selector_and_clean_context(self):
        self.assertNotIn("agent" + "_name", self.text)
        self.assertIn("agent_type", self.text)
        self.assertIn('fork_turns="none"', self.text)
        self.assertIn("fork_context=false", self.text)
        self.assertIn("если доступная схема spawn не содержит `agent_type`", self.lower)
        self.assertIn("критически остановись", self.lower)
        self.assertIn("не передавай роль через `task_name`", self.lower)
        self.assertIn("не запускай generic agent", self.lower)
        self.assertIn("только заполненный шаблон", self.lower)
        self.assertIn("запуск с наследованием истории по умолчанию запрещён", self.lower)

    def test_global_start_message_shape_names_every_allowed_part(self):
        rules = self.text.split("## Неподвижные правила", 1)[1].split("## Порядок одной главы", 1)[0]
        shape = next(line for line in rules.splitlines() if "Стартовое задание" in line)
        self.assertRegex(
            shape,
            r"финальн\w+ инструкц\w+ `Не используй сведения вне перечисленных файлов\.`",
        )
        for phrase in (
            "роль",
            "абсолютные пути необходимых входов",
            "ровно один абсолютный путь ожидаемого output",
            "метаданные spawn находятся вне стартового сообщения",
        ):
            self.assertIn(phrase.casefold(), shape.casefold())

    def test_fresh_agents_are_strictly_sequential(self):
        required_in_order = [
            "book_translator_translator",
            "book_translator_verifier",
            "book_translator_editor",
            "book_translator_verifier",
            "book_translator_editor",
            "book_translator_verifier",
            "book_translator_state_updater",
        ]
        start = 0
        for name in required_in_order:
            position = self.text.find(name, start)
            self.assertGreaterEqual(position, 0, name)
            start = position + len(name)
        self.assertRegex(self.lower, r"чист(ым|ой) контекст")
        self.assertRegex(self.lower, r"никогда[^\n]*параллел")
        self.assertIn("не готовь следующую главу заранее", self.lower)
        self.assertIn("не передавай историю", self.lower)
        self.assertIn("не выполняй художественный перевод", self.lower)
        self.assertIn("не подменяй", self.lower)

    def test_long_chapter_uses_one_translator_for_all_chunks(self):
        self.assertIn("60000", self.text)
        self.assertIn("split_blocks", self.text)
        self.assertIn("только между блоками", self.lower)
        self.assertIn("один экземпляр translator", self.lower)
        self.assertIn("части главы последовательно", self.lower)
        self.assertIn("chunks-manifest.json", self.text)
        self.assertIn("контекстный хвост", self.lower)
        self.assertIn("не записывай повторно", self.lower)
        self.assertIn("не запускай отдельного translator", self.lower)

    def test_two_edits_are_the_limit_and_critical_failure_blocks_publication(self):
        self.assertIn("ровно один дополнительный", self.lower)
        self.assertIn("третья редактура запрещена", self.lower)
        self.assertIn("оставшейся критической ошибке", self.lower)
        self.assertIn("остановись", self.lower)
        self.assertIn("сохрани отчёт", self.lower)
        self.assertIn("не публикуй главу", self.lower)

    def test_second_edit_and_third_verifier_advance_explicit_stages(self):
        workflow = self.text.split("## Порядок одной главы", 1)[1].split("## Длинная глава", 1)[0]
        required_in_order = (
            "request_second_edit",
            "edited-2.json",
            "validate_translation",
            'advance_stage(project, "редактура_2", artifact=edited_2)',
            "report-3.json",
            'advance_stage(project, "проверка_3", artifact=report_3)',
            "record_failure",
            "сборка",
        )
        start = 0
        for phrase in required_in_order:
            position = workflow.find(phrase, start)
            self.assertGreaterEqual(position, 0, phrase)
            start = position + len(phrase)

    def test_state_changes_only_after_an_accepted_built_chapter(self):
        self.assertIn("только один раз", self.lower)
        self.assertIn("окончательно принят", self.lower)
        self.assertIn("не после части", self.lower)
        self.assertIn("не изменяя действующий state", self.lower)
        self.assertIn("{chapter_work}/next-state/", self.text)
        self.assertIn("проверь все файлы в `next-state/`", self.lower)
        self.assertIn("next_state=next_state", self.text)
        self.assertIn("commit_transaction", self.text)
        self.assertIn("result_name=built_document.name", self.text)
        self.assertIn("только после успешной фиксации", self.lower)

    def test_start_tasks_use_absolute_inputs_and_exactly_one_output(self):
        section_matches = re.findall(
            r"(?ms)^### Шаблон: ([^\n]+)\n(.*?)(?=^### Шаблон: |^## Самопроверка координатора)",
            self.text,
        )
        expected = {
            "translator": {
                "agent": "book_translator_translator",
                "task": "book-translation-{chapter_id}-translator",
                "inputs": ("translation-principles.md", "source-blocks.json", "chunks-manifest.json", "{project}/state/"),
                "input_lines": 3,
                "output": "{chapter_work}/draft.json",
                "forbidden": ("report-", "edited-", "accepted.json", "{final_translation}"),
            },
            "verifier 1": {
                "agent": "book_translator_verifier",
                "task": "book-translation-{chapter_id}-verifier-1",
                "inputs": ("verification-rules.md", "source-blocks.json", "draft.json", "{project}/state/"),
                "input_lines": 4,
                "output": "{chapter_work}/report-1.json",
                "forbidden": ("report-", "edited-", "accepted.json", "{final_translation}"),
            },
            "editor 1": {
                "agent": "book_translator_editor",
                "task": "book-translation-{chapter_id}-editor-1",
                "inputs": ("translation-principles.md", "source-blocks.json", "draft.json", "report-1.json", "{project}/state/"),
                "input_lines": 5,
                "output": "{chapter_work}/edited-1.json",
                "forbidden": ("report-2.json", "report-3.json", "edited-", "accepted.json", "{final_translation}"),
            },
            "verifier 2": {
                "agent": "book_translator_verifier",
                "task": "book-translation-{chapter_id}-verifier-2",
                "inputs": ("verification-rules.md", "source-blocks.json", "edited-1.json", "{project}/state/"),
                "input_lines": 4,
                "output": "{chapter_work}/report-2.json",
                "forbidden": ("report-", "draft.json", "edited-2.json", "accepted.json", "{final_translation}"),
            },
            "editor 2": {
                "agent": "book_translator_editor",
                "task": "book-translation-{chapter_id}-editor-2",
                "inputs": ("translation-principles.md", "source-blocks.json", "edited-1.json", "report-2.json", "{project}/state/"),
                "input_lines": 5,
                "output": "{chapter_work}/edited-2.json",
                "forbidden": ("report-1.json", "report-3.json", "draft.json", "accepted.json", "{final_translation}"),
            },
            "verifier 3": {
                "agent": "book_translator_verifier",
                "task": "book-translation-{chapter_id}-verifier-3",
                "inputs": ("verification-rules.md", "source-blocks.json", "edited-2.json", "{project}/state/"),
                "input_lines": 4,
                "output": "{chapter_work}/report-3.json",
                "forbidden": ("report-", "draft.json", "edited-1.json", "accepted.json", "{final_translation}"),
            },
            "state-updater": {
                "agent": "book_translator_state_updater",
                "task": "book-translation-{chapter_id}-state-updater",
                "inputs": ("source-blocks.json", "{final_translation}", "{project}/state/"),
                "input_lines": 3,
                "output": "{chapter_work}/next-state/",
                "forbidden": ("report-", "draft.json", "edited-", "accepted.json"),
            },
        }
        headings = [heading for heading, _ in section_matches]
        self.assertEqual(7, len(section_matches))
        self.assertEqual(set(expected), set(headings))
        for heading in expected:
            self.assertEqual(1, headings.count(heading), heading)

        for heading, section in section_matches:
            with self.subTest(template=heading):
                contract = expected[heading]
                blocks = re.findall(r"```text\n(.*?)\n```", section, flags=re.DOTALL)
                self.assertEqual(1, len(blocks))
                template = blocks[0]
                prelude = section.split("```text", 1)[0]
                input_lines = [line for line in template.splitlines() if line.startswith("Абсолютный путь")]
                output_lines = [line for line in template.splitlines() if line.startswith("Единственный output:")]
                inputs = "\n".join(input_lines)

                self.assertIn(f"Custom agent: {contract['agent']}", prelude)
                self.assertIn(
                    f'V2: task_name="{contract["task"]}", agent_type="{contract["agent"]}", '
                    'fork_turns="none", message="заполненный шаблон ниже".',
                    prelude,
                )
                self.assertIn(
                    f'V1: agent_type="{contract["agent"]}", fork_context=false, '
                    'message="заполненный шаблон ниже".',
                    prelude,
                )
                self.assertIn("Стартовое сообщение — только заполненный шаблон ниже", prelude)
                for phrase in ("роль", "абсолютные пути входов", "ровно один абсолютный путь output", "финальная инструкция"):
                    self.assertIn(phrase, prelude.casefold())
                self.assertNotIn("только приведённые ниже абсолютные пути", prelude.casefold())

                self.assertEqual(contract["input_lines"], len(input_lines))
                for token in contract["inputs"]:
                    self.assertIn(token, inputs)
                if heading == "translator":
                    self.assertIn("либо", inputs)
                for token in contract["forbidden"]:
                    self.assertNotIn(token, inputs)
                for token in ("turns", "истори", "стенограмм", "рассужден", "свободн"):
                    self.assertNotIn(token, template.casefold())

                self.assertEqual([f"Единственный output: {contract['output']}"], output_lines)
                self.assertTrue(template.endswith("Не используй сведения вне перечисленных файлов."))


class UniversalReferenceTests(unittest.TestCase):
    def test_translation_principles_cover_literary_contract(self):
        text = (REFERENCES / "translation-principles.md").read_text(encoding="utf-8").casefold()
        for phrase in (
            "смысл, интонация и художественный эффект",
            "англоязычную кальку",
            "голоса рассказчика и персонажей",
            "повторы, паузы, обрывы и ритм",
            "неоднозначность",
            "отсутствие цензуры",
            "русская прямая речь",
            "метафоры",
            "добавления и украшательства",
            "естественной русской прозы",
        ):
            self.assertIn(phrase, text)

    def test_manual_project_glossary_has_priority(self):
        text = (REFERENCES / "translation-principles.md").read_text(encoding="utf-8").casefold()
        self.assertIn("glossary.md", text)
        self.assertIn("абсолютный приоритет", text)
        self.assertIn("установленные имена и термины", text)
        self.assertIn("последовательно", text)
        self.assertIn("decisions.md", text)
        self.assertIn("не меняй молча", text)

    def test_verification_has_three_outcomes_and_mechanical_precedence(self):
        text = (REFERENCES / "verification-rules.md").read_text(encoding="utf-8").casefold()
        self.assertIn("## критическая ошибка", text)
        self.assertIn("## требует редактуры", text)
        self.assertIn("## пройдено", text)
        self.assertIn("механическая структура", text)
        self.assertIn("не количество предложений", text)

    def test_references_contain_no_specific_book_data(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in REFERENCES.glob("*.md")
        ).casefold()
        for forbidden in ("илиш", "магнус", "дрейк", "кел", "джейд", "силас", "coke"):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
