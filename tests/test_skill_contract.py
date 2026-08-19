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
        self.assertIn("/book-translator [ПАПКА] [формат: pages|docx|как-в-оригинале] [продолжить|начать-заново]", self.text)
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

    def test_state_changes_only_after_an_accepted_built_chapter(self):
        self.assertIn("только один раз", self.lower)
        self.assertIn("окончательно принят", self.lower)
        self.assertIn("не после части", self.lower)
        self.assertIn("не изменяя действующий state", self.lower)
        self.assertIn("commit_transaction", self.text)
        self.assertIn("только после успешной фиксации", self.lower)

    def test_start_tasks_use_absolute_inputs_and_exactly_one_output(self):
        templates = re.findall(
            r"### Шаблон: .+?\n\n```text\n(.*?)\n```",
            self.text,
            flags=re.DOTALL,
        )
        self.assertEqual(7, len(templates))
        for template in templates:
            with self.subTest(template=template.splitlines()[0]):
                self.assertIn("Абсолютный путь", template)
                self.assertEqual(1, template.count("Единственный output:"))
                self.assertTrue(template.endswith("Не используй сведения вне перечисленных файлов."))
        self.assertNotIn("report-1.json", templates[3])
        self.assertNotIn("report-1.json", templates[5])


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
