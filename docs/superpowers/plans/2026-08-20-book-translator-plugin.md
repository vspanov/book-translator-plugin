# План реализации плагина последовательного перевода книг

> **Для агентных исполнителей:** ОБЯЗАТЕЛЬНЫЙ ДОПОЛНИТЕЛЬНЫЙ НАВЫК: используйте `superpowers:subagent-driven-development` (рекомендуется) либо `superpowers:executing-plans` и выполняйте задачи по одной. Отмечайте шаги флажками `- [ ]`.

**Цель:** создать локальный плагин Codex, который последовательно переводит английские главы в русские `.docx` и `.pages`, проверяет полноту, сохраняет поддерживаемое оформление и безопасно возобновляет прерванную работу.

**Архитектура:** основной skill управляет конвейером и строго по одному запускает четыре custom agents с чистым контекстом. Два небольших сценария отвечают за документы и состояние проекта; детерминированный `Stop`-hook проверяет согласованность, но ничего не переводит. Состояние и обмен между этапами хранятся в русскоязычных Markdown/JSON-файлах внутри проекта книги.

**Технологии:** Python 3, `python-docx`, стандартные `json`, `zipfile`, `hashlib`, `configparser`, `pathlib`, `shutil`, `subprocess`, `unittest`; AppleScript и Pages только на macOS; TOML custom agents; JSON-манифест плагина и hook.

**Спецификация:** `docs/superpowers/specs/2026-08-19-book-translator-plugin-design.md`

## Общие ограничения

- Всё читаемое человеком содержимое плагина, сообщения, комментарии, инструкции и проектные шаблоны пишутся по-русски; английскими остаются имена файлов и обязательные элементы синтаксиса.
- Поддерживаемая языковая пара первой версии — английский → русский.
- `.docx` работает на macOS и Windows; `.pages` работает только на macOS через установленное приложение Pages и только после разрешения пользователя на запуск внешнего приложения.
- Главы и ролевые агенты обрабатываются строго последовательно; параллельный запуск запрещён.
- Каждый ролевой этап использует новый custom agent с чистой историей; модель и уровень рассуждения не закрепляются в TOML.
- Исходники никогда не изменяются, не перемещаются и не входят в операции очистки.
- Глава публикуется только после механической и смысловой проверки, сборки документа, подготовки следующей памяти и успешной фиксации контрольной точки.
- Плагин не устанавливает `python-docx` и не запускает Pages без явного разрешения.
- Сценарии требуют Python 3.10 или новее; предварительная проверка показывает фактический путь и версию до обработки первой главы.
- В первой версии нет MCP-сервера, отдельного API, графического интерфейса, CAT-интеграций и дополнительных языковых пар.
- Тесты используют стандартный `unittest`; новый тестовый фреймворк не добавляется.

## Карта файлов

| Файл | Ответственность |
|---|---|
| `.codex-plugin/plugin.json` | Имя, версия, описание, каталог skills и bundled hook. |
| `requirements.txt` | Единственная внешняя Python-зависимость: `python-docx`. |
| `skills/book-translator/SKILL.md` | Разбор пользовательского запроса и последовательная координация всего конвейера. |
| `skills/book-translator/references/translation-principles.md` | Универсальные правила художественного перевода из пользовательского prompt. |
| `skills/book-translator/references/verification-rules.md` | Правила смысловой и механической проверки. |
| `skills/book-translator/assets/*` | Русские шаблоны проекта книги и безопасная конфигурация по умолчанию. |
| `skills/book-translator/scripts/documents.py` | Обнаружение глав, манифест, `.docx`, внутренние блоки, полнота и вызов Pages-моста. |
| `skills/book-translator/scripts/progress.py` | Инициализация проекта, этапы, контрольные точки, транзакции, восстановление и перезапуск. |
| `skills/book-translator/scripts/install-agents.py` | Предпросмотр и подтверждаемая установка четырёх TOML-файлов. |
| `skills/book-translator/scripts/pages-bridge.applescript` | Экспорт Pages → DOCX и импорт DOCX → Pages. |
| `agents/*.toml` | Четыре узкие русскоязычные роли без закреплённой модели. |
| `hooks/hooks.json` | Регистрация единственного `Stop`-hook. |
| `hooks/check-progress.py` | Проверка активного проекта и JSON-ответ для события `Stop`. |
| `tests/*.py` | Переносимые модульные и интеграционные проверки. |
| `tests/acceptance/*` | Явные проверки с реальным Codex и Pages, которые нельзя безопасно запускать скрыто. |
| `README.md` | Русская установка, запуск, ограничения и восстановление. |

---

### Задача 1. Минимальный каркас плагина и проверка русскоязычного контракта

**Файлы:**

- Создать: `.codex-plugin/plugin.json`
- Создать: `requirements.txt`
- Создать: `tests/test_plugin_structure.py`

**Интерфейсы:**

- Потребляет: утверждённую спецификацию.
- Производит: манифест версии `0.1.0`, каталог skills `./skills/`, hook `./hooks/hooks.json` и общий тест структуры для последующих задач.

- [ ] **Шаг 1. Написать падающий тест манифеста**

```python
# tests/test_plugin_structure.py
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Шаг 2. Запустить тест и подтвердить ожидаемое падение**

Выполнить: `python -m unittest tests.test_plugin_structure -v`

Ожидается: ошибка отсутствия `.codex-plugin/plugin.json`.

- [ ] **Шаг 3. Создать минимальный манифест и список зависимости**

```json
{
  "name": "book-translator-plugin",
  "version": "0.1.0",
  "description": "Последовательный художественный перевод английских книг на русский язык с проверкой полноты и сохранением оформления",
  "skills": "./skills/",
  "hooks": "./hooks/hooks.json"
}
```

```text
python-docx>=1.2,<2
```

- [ ] **Шаг 4. Запустить тест и подтвердить прохождение**

Выполнить: `python -m unittest tests.test_plugin_structure -v`

Ожидается: `OK`, 2 теста.

- [ ] **Шаг 5. Зафиксировать каркас**

```bash
git add .codex-plugin/plugin.json requirements.txt tests/test_plugin_structure.py
git commit -m "feat: добавить минимальный манифест плагина"
```

### Задача 2. Русские шаблоны и безопасная инициализация проекта книги

**Файлы:**

- Создать: `skills/book-translator/assets/project-readme.md`
- Создать: `skills/book-translator/assets/characters.md`
- Создать: `skills/book-translator/assets/glossary.md`
- Создать: `skills/book-translator/assets/style-guide.md`
- Создать: `skills/book-translator/assets/story-state.md`
- Создать: `skills/book-translator/assets/chapter-summaries.md`
- Создать: `skills/book-translator/assets/decisions.md`
- Создать: `skills/book-translator/assets/config.ini`
- Создать: `skills/book-translator/scripts/progress.py`
- Создать: `tests/test_progress.py`

**Интерфейсы:**

- Потребляет: каталог assets относительно `progress.py`.
- Производит: `initialize_project(project_dir: Path) -> None`, создающий `output/`, `state/`, `work/`, `config.ini`, `progress.json`, `README.md` без перезаписи существующих файлов.

- [ ] **Шаг 1. Написать падающие тесты инициализации**

```python
# tests/test_progress.py
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
```

- [ ] **Шаг 2. Запустить тест и подтвердить ожидаемое падение**

Выполнить: `python -m unittest tests.test_progress.ProjectInitializationTests -v`

Ожидается: `ModuleNotFoundError: No module named 'progress'`.

- [ ] **Шаг 3. Создать шаблоны с фиксированными разделами**

Использовать следующие первые заголовки и не добавлять сведения о конкретной книге:

```markdown
# Персонажи

## Подтверждённые сведения

## Голоса и манера речи

## Отношения и обращения
```

```markdown
# Глоссарий

| Английский вариант | Канонический русский вариант | Склонение и примечания |
|---|---|---|
```

```markdown
# Руководство по стилю

## Голос рассказчика

## Регистр, ритм и типографика

## Повторы, паузы и обрывы
```

Остальные файлы начинаютcя с `# Состояние сюжета`, `# Краткие содержания глав`, `# Переводческие решения` и `# Проект перевода книги`. В `decisions.md` создать разделы `## Принятые решения` и `## Нерешённые вопросы`.

```ini
[перевод]
выходной_формат = как_в_оригинале
режим = продолжить
```

- [ ] **Шаг 4. Реализовать инициализацию без перезаписи**

```python
# ключевой контракт skills/book-translator/scripts/progress.py
from __future__ import annotations

import json
import shutil
from pathlib import Path


ASSET_NAMES = {
    "project-readme.md": "README.md",
    "config.ini": "config.ini",
}
STATE_ASSETS = (
    "characters.md",
    "glossary.md",
    "style-guide.md",
    "story-state.md",
    "chapter-summaries.md",
    "decisions.md",
)


def initialize_project(project_dir: Path) -> None:
    project_dir = project_dir.resolve()
    assets = Path(__file__).resolve().parents[1] / "assets"
    for name in ("output", "state", "work"):
        (project_dir / name).mkdir(parents=True, exist_ok=True)
    for source_name, target_name in ASSET_NAMES.items():
        target = project_dir / target_name
        if not target.exists():
            shutil.copy2(assets / source_name, target)
    for name in STATE_ASSETS:
        target = project_dir / "state" / name
        if not target.exists():
            shutil.copy2(assets / name, target)
    progress_path = project_dir / "progress.json"
    if not progress_path.exists():
        write_json_atomic(progress_path, {
            "версия": 1,
            "статус_книги": "не_начат",
            "текущая_глава": None,
            "этап": None,
            "последняя_готовая_глава": None,
            "ошибка": None,
        })


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
```

- [ ] **Шаг 5. Запустить тесты задачи**

Выполнить: `python -m unittest tests.test_progress.ProjectInitializationTests -v`

Ожидается: `OK`, 2 теста.

- [ ] **Шаг 6. Зафиксировать шаблоны и инициализацию**

```bash
git add skills/book-translator/assets skills/book-translator/scripts/progress.py tests/test_progress.py
git commit -m "feat: инициализировать проект перевода"
```

### Задача 3. Обнаружение, естественная сортировка и фиксация манифеста глав

**Файлы:**

- Создать: `skills/book-translator/scripts/documents.py`
- Создать: `tests/test_chapter_discovery.py`

**Интерфейсы:**

- Потребляет: путь проекта после `initialize_project`.
- Производит: `discover_chapters(project_dir: Path) -> list[Path]`, `natural_key(path: Path) -> tuple`, `build_manifest(project_dir: Path, chapters: list[Path]) -> dict`, `verify_manifest(project_dir: Path, manifest: dict) -> list[str]` и `check_output_conflicts(project_dir: Path, chapters: list[Path], output_format: str) -> list[str]`.
- Манифест: `work/manifest.json` с русскими ключами `версия`, `источник`, `главы`; каждая глава содержит `номер`, `имя`, `путь`, `sha256`.

- [ ] **Шаг 1. Написать падающие тесты обнаружения**

```python
# tests/test_chapter_discovery.py
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/book-translator/scripts"
sys.path.insert(0, str(SCRIPTS))
import documents


class ChapterDiscoveryTests(unittest.TestCase):
    def test_natural_order_places_2_before_10(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            for name in ("chapter-10.docx", "chapter-2.docx", "chapter-1.docx"):
                (project / name).write_bytes(b"docx")
            names = [path.name for path in documents.discover_chapters(project)]
            self.assertEqual(["chapter-1.docx", "chapter-2.docx", "chapter-10.docx"], names)

    def test_input_and_root_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "input").mkdir()
            (project / "input/chapter-1.docx").write_bytes(b"docx")
            (project / "chapter-2.docx").write_bytes(b"docx")
            with self.assertRaisesRegex(ValueError, "одновременно"):
                documents.discover_chapters(project)

    def test_duplicate_casefolded_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "Chapter-1.docx").write_bytes(b"one")
            (project / "chapter-1.docx").write_bytes(b"two")
            with self.assertRaisesRegex(ValueError, "неоднознач"):
                documents.discover_chapters(project)
```

- [ ] **Шаг 2. Запустить тест и подтвердить ожидаемое падение**

Выполнить: `python -m unittest tests.test_chapter_discovery -v`

Ожидается: `ModuleNotFoundError: No module named 'documents'`.

- [ ] **Шаг 3. Реализовать обнаружение и естественную сортировку**

```python
# ключевые функции documents.py
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


SUPPORTED_SUFFIXES = {".docx", ".pages"}
EXCLUDED_DIRECTORIES = {"output", "state", "work"}


def natural_key(path: Path) -> tuple:
    return tuple(int(part) if part.isdigit() else part.casefold()
                 for part in re.split(r"(\d+)", path.name))


def discover_chapters(project_dir: Path) -> list[Path]:
    project_dir = project_dir.resolve()
    input_dir = project_dir / "input"
    root_files = [p for p in project_dir.iterdir()
                  if p.is_file() and p.suffix.casefold() in SUPPORTED_SUFFIXES]
    input_files = [] if not input_dir.is_dir() else [
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.casefold() in SUPPORTED_SUFFIXES
    ]
    if root_files and input_files:
        raise ValueError("Поддерживаемые документы найдены одновременно в input/ и корне проекта.")
    chapters = input_files or root_files
    folded = [path.name.casefold() for path in chapters]
    if len(folded) != len(set(folded)):
        raise ValueError("Имена глав неоднозначны с учётом регистра.")
    if not chapters:
        raise ValueError("Поддерживаемые главы .docx или .pages не найдены.")
    return sorted(chapters, key=natural_key)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

`build_manifest` записывает относительные POSIX-пути, чтобы проект можно было перенести между macOS и Windows. `verify_manifest` возвращает русские ошибки для удалённой, изменённой или вставленной не в конец главы; допустимое добавление только в конец обновляет манифест отдельной явной операцией координатора. `check_output_conflicts` вычисляет конечное расширение каждой главы и останавливает запуск, если два источника дают одно имя результата или существующий файл не подтверждён текущей контрольной точкой.

- [ ] **Шаг 4. Добавить тесты контрольных сумм и добавления только в конец**

```python
    def test_changed_completed_source_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            chapter = project / "chapter-1.docx"
            chapter.write_bytes(b"first")
            manifest = documents.build_manifest(project, [chapter])
            chapter.write_bytes(b"changed")
            errors = documents.verify_manifest(project, manifest)
            self.assertTrue(any("изменена" in error for error in errors))

    def test_new_chapter_is_allowed_only_after_existing_order(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            first = project / "chapter-2.docx"
            first.write_bytes(b"first")
            manifest = documents.build_manifest(project, [first])
            inserted = project / "chapter-1.docx"
            inserted.write_bytes(b"inserted")
            self.assertTrue(any("после последней" in error
                                for error in documents.verify_manifest(project, manifest)))
```

- [ ] **Шаг 5. Запустить тесты обнаружения**

Выполнить: `python -m unittest tests.test_chapter_discovery -v`

Ожидается: `OK`, 5 тестов.

- [ ] **Шаг 6. Зафиксировать обнаружение глав**

```bash
git add skills/book-translator/scripts/documents.py tests/test_chapter_discovery.py
git commit -m "feat: обнаруживать и фиксировать порядок глав"
```

### Задача 4. Внутренние блоки DOCX и механическая проверка полноты

**Файлы:**

- Изменить: `skills/book-translator/scripts/documents.py`
- Создать: `tests/test_docx_blocks.py`

**Интерфейсы:**

- Потребляет: исходный `.docx` и путь рабочего JSON.
- Производит: `extract_docx(source: Path, destination: Path) -> list[dict]`, `load_blocks(path: Path) -> list[dict]`, `validate_translation(source_blocks: list[dict], translated_blocks: list[dict]) -> list[str]`, `split_blocks(blocks: list[dict], max_chars: int) -> list[list[dict]]`.
- Формат блока: русские ключи `идентификатор`, `тип`, `стиль`, `фрагменты`; фрагмент содержит `текст`, `курсив`, `полужирный`, `сноска`. Основные блоки получают `B000001...`, а переводимые абзацы сносок — `F<идентификатор-сноски>-P<номер-абзаца>` и тип `сноска`.

- [ ] **Шаг 1. Написать падающие тесты извлечения и полноты**

```python
# tests/test_docx_blocks.py
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/book-translator/scripts"
sys.path.insert(0, str(SCRIPTS))
import documents


def make_docx(path: Path) -> None:
    document = Document()
    document.add_heading("Chapter One", level=1)
    paragraph = document.add_paragraph()
    paragraph.add_run("Quiet ")
    italic = paragraph.add_run("thought")
    italic.italic = True
    bold = paragraph.add_run(" became an order.")
    bold.bold = True
    scene = document.add_paragraph("* * *")
    scene.style = document.styles["Normal"]
    document.add_paragraph("After the break.")
    document.save(path)


class DocxBlockTests(unittest.TestCase):
    def test_extracts_stable_blocks_and_run_formatting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "chapter.docx"
            target = root / "blocks.json"
            make_docx(source)
            blocks = documents.extract_docx(source, target)
            self.assertEqual(["B000001", "B000002", "B000003", "B000004"],
                             [block["идентификатор"] for block in blocks])
            self.assertEqual("заголовок", blocks[0]["тип"])
            self.assertTrue(blocks[1]["фрагменты"][1]["курсив"])
            self.assertTrue(blocks[1]["фрагменты"][2]["полужирный"])
            self.assertEqual("разрыв_сцены", blocks[2]["тип"])

    def test_rejects_missing_empty_duplicate_reordered_and_unknown_blocks(self):
        source = [
            {"идентификатор": "B000001", "тип": "абзац", "стиль": "Normal",
             "фрагменты": [{"текст": "One", "курсив": False, "полужирный": False, "сноска": None}]},
            {"идентификатор": "B000002", "тип": "абзац", "стиль": "Normal",
             "фрагменты": [{"текст": "Two", "курсив": False, "полужирный": False, "сноска": None}]},
        ]
        variants = {
            "отсутствует": source[:1],
            "пуст": [{**source[0], "фрагменты": [{**source[0]["фрагменты"][0], "текст": ""}]}, source[1]],
            "повтор": [source[0], source[0], source[1]],
            "поряд": list(reversed(source)),
            "неизвест": source + [{**copy.deepcopy(source[1]), "идентификатор": "B999999"}],
        }
        for word, translated in variants.items():
            with self.subTest(word=word):
                self.assertTrue(any(word in error.lower()
                                    for error in documents.validate_translation(source, translated)))
```

- [ ] **Шаг 2. Запустить тест и подтвердить ожидаемое падение**

Выполнить: `python -m unittest tests.test_docx_blocks -v`

Ожидается: `AttributeError: module 'documents' has no attribute 'extract_docx'`.

- [ ] **Шаг 3. Реализовать JSON-блоки и валидацию**

```python
def _block_type(paragraph) -> str:
    text = paragraph.text.strip()
    style = (paragraph.style.name or "") if paragraph.style else ""
    if style.casefold().startswith("heading"):
        return "заголовок"
    if text in {"***", "* * *", "— — —"} or "scene" in style.casefold():
        return "разрыв_сцены"
    return "абзац"


def validate_translation(source_blocks: list[dict], translated_blocks: list[dict]) -> list[str]:
    errors: list[str] = []
    expected = [block["идентификатор"] for block in source_blocks]
    actual = [block.get("идентификатор") for block in translated_blocks]
    for identifier in expected:
        count = actual.count(identifier)
        if count == 0:
            errors.append(f"Блок {identifier} отсутствует.")
        elif count > 1:
            errors.append(f"Блок {identifier} повторён.")
    for identifier in actual:
        if identifier not in expected:
            errors.append(f"Обнаружен неизвестный блок {identifier}.")
    if actual != expected and set(actual) == set(expected) and len(actual) == len(expected):
        errors.append("Порядок блоков изменён.")
    source_by_id = {block["идентификатор"]: block for block in source_blocks}
    for block in translated_blocks:
        identifier = block.get("идентификатор")
        if identifier not in source_by_id:
            continue
        source_text = "".join(part["текст"] for part in source_by_id[identifier]["фрагменты"])
        translated_text = "".join(part.get("текст", "") for part in block.get("фрагменты", []))
        if source_text.strip() and not translated_text.strip():
            errors.append(f"Перевод блока {identifier} пуст.")
        if _format_signature(source_by_id[identifier]) != _format_signature(block):
            errors.append(f"Маркеры оформления блока {identifier} изменены.")
    return errors
```

`_format_signature` сравнивает количество фрагментов и тройки `(курсив, полужирный, сноска)`; текст в сигнатуру не входит. `extract_docx` читает `word/footnotes.xml`, добавляет каждый пользовательский абзац сноски как переводимый блок сразу после основного блока с соответствующей ссылкой и не добавляет служебные сноски `-1` и `0`. JSON сохраняется в UTF-8 с `ensure_ascii=False`. `split_blocks` режет только между блоками, удерживает блок сноски рядом с блоком-ссылкой и никогда не создаёт дубликаты.

- [ ] **Шаг 4. Добавить тест разбиения длинной главы**

```python
    def test_split_blocks_never_splits_or_duplicates_a_block(self):
        blocks = [
            {"идентификатор": f"B{index:06d}", "тип": "абзац", "стиль": "Normal",
             "фрагменты": [{"текст": "x" * 20, "курсив": False, "полужирный": False, "сноска": None}]}
            for index in range(1, 6)
        ]
        chunks = documents.split_blocks(blocks, max_chars=45)
        flattened = [block["идентификатор"] for chunk in chunks for block in chunk]
        self.assertEqual([block["идентификатор"] for block in blocks], flattened)
        self.assertEqual([2, 2, 1], [len(chunk) for chunk in chunks])
```

- [ ] **Шаг 5. Запустить тесты блоков**

Выполнить: `python -m unittest tests.test_docx_blocks -v`

Ожидается: `OK`, 3 теста.

- [ ] **Шаг 6. Зафиксировать внутренний формат и полноту**

```bash
git add skills/book-translator/scripts/documents.py tests/test_docx_blocks.py
git commit -m "feat: извлекать и проверять блоки docx"
```

### Задача 5. Обратная сборка DOCX с сохранением поддерживаемого оформления

**Файлы:**

- Изменить: `skills/book-translator/scripts/documents.py`
- Создать: `tests/docx_fixture.py`
- Изменить: `tests/test_docx_blocks.py`

**Интерфейсы:**

- Потребляет: исходный шаблон `.docx` и проверенный список блоков.
- Производит: `rebuild_docx(template: Path, translated_blocks: list[dict], destination: Path) -> None` и `inspect_docx(path: Path) -> list[str]`.
- Гарантирует: сохранение порядка абзацев, стилей, курсива, полужирного текста, разрывов сцен, ссылок на сноски и перевод текста сносок; неподдерживаемые конструкции возвращаются русскими предупреждениями до перевода.

- [ ] **Шаг 1. Дополнить фикстуру одной сноской и написать падающий тест round-trip**

`tests/docx_fixture.py` создаёт обычный документ через `python-docx`, затем стандартным `zipfile` добавляет `word/footnotes.xml`, связь типа `.../relationships/footnotes` и `w:footnoteReference` в последний абзац. Идентификатор пользовательской сноски — `2`; системные сноски `-1` и `0` сохраняются как служебные.

```python
# ключевой код tests/docx_fixture.py
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree as ET

from docx import Document


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def _rewrite_zip(path: Path, replacements: dict[str, bytes]) -> None:
    with NamedTemporaryFile(delete=False, suffix=".docx", dir=path.parent) as temporary:
        temporary_path = Path(temporary.name)
    with ZipFile(path, "r") as source, ZipFile(temporary_path, "w", ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item, replacements.get(item.filename, source.read(item.filename)))
        for name, content in replacements.items():
            if name not in source.namelist():
                target.writestr(name, content)
    temporary_path.replace(path)


def make_formatted_docx(path: Path) -> None:
    document = Document()
    document.add_heading("Chapter One", level=1)
    paragraph = document.add_paragraph()
    paragraph.add_run("Quiet ")
    italic = paragraph.add_run("thought")
    italic.italic = True
    bold = paragraph.add_run(" became an order.")
    bold.bold = True
    document.add_paragraph("* * *")
    document.add_paragraph("After the break.")
    document.save(path)

    with ZipFile(path, "r") as package:
        content_types = ET.fromstring(package.read("[Content_Types].xml"))
        ET.SubElement(content_types, f"{{{CT}}}Override", {
            "PartName": "/word/footnotes.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
        })
        relationships = ET.fromstring(package.read("word/_rels/document.xml.rels"))
        ET.SubElement(relationships, f"{{{REL}}}Relationship", {
            "Id": "rIdFootnotes",
            "Type": f"{R}/footnotes",
            "Target": "footnotes.xml",
        })
        body = ET.fromstring(package.read("word/document.xml"))
    paragraph_xml = body.findall(f".//{{{W}}}p")[-1]
    run = ET.SubElement(paragraph_xml, f"{{{W}}}r")
    ET.SubElement(run, f"{{{W}}}footnoteReference", {f"{{{W}}}id": "2"})
    footnotes = ET.fromstring(
        f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="-1"/>'
        f'<w:footnote w:id="0"/><w:footnote w:id="2"><w:p><w:r>'
        f'<w:t>Original footnote.</w:t></w:r></w:p></w:footnote></w:footnotes>'
    )
    _rewrite_zip(path, {
        "[Content_Types].xml": ET.tostring(content_types, encoding="utf-8", xml_declaration=True),
        "word/_rels/document.xml.rels": ET.tostring(relationships, encoding="utf-8", xml_declaration=True),
        "word/document.xml": ET.tostring(body, encoding="utf-8", xml_declaration=True),
        "word/footnotes.xml": ET.tostring(footnotes, encoding="utf-8", xml_declaration=True),
    })
```

```python
# фрагмент tests/test_docx_blocks.py
from tests.docx_fixture import make_formatted_docx


class DocxRoundTripTests(unittest.TestCase):
    def test_rebuild_preserves_supported_formatting_and_footnote_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.docx"
            result = root / "result.docx"
            blocks_path = root / "blocks.json"
            make_formatted_docx(source)
            blocks = documents.extract_docx(source, blocks_path)
            translated = copy.deepcopy(blocks)
            for block in translated:
                for fragment in block["фрагменты"]:
                    if fragment["текст"].strip():
                        fragment["текст"] = ("Переведённая сноска"
                                             if block["тип"] == "сноска" else "Перевод")
            self.assertEqual([], documents.validate_translation(blocks, translated))
            documents.rebuild_docx(source, translated, result)
            self.assertEqual([], documents.inspect_docx(result))
            rebuilt = Document(result)
            self.assertEqual("Heading 1", rebuilt.paragraphs[0].style.name)
            self.assertTrue(rebuilt.paragraphs[1].runs[1].italic)
            self.assertTrue(rebuilt.paragraphs[1].runs[2].bold)
            self.assertTrue(documents.docx_has_footnote_reference(result, "2"))
            self.assertIn("Переведённая сноска", documents.docx_footnote_text(result, "2"))
```

- [ ] **Шаг 2. Запустить round-trip и подтвердить ожидаемое падение**

Выполнить: `python -m unittest tests.test_docx_blocks.DocxRoundTripTests -v`

Ожидается: отсутствие `rebuild_docx`.

- [ ] **Шаг 3. Реализовать замену текста без уничтожения XML-узлов оформления**

```python
def rebuild_docx(template: Path, translated_blocks: list[dict], destination: Path) -> None:
    from docx import Document

    document = Document(template)
    main_blocks = [block for block in translated_blocks if block["тип"] != "сноска"]
    if len(document.paragraphs) != len(main_blocks):
        raise ValueError("Количество блоков шаблона не совпадает с проверенным переводом.")
    for paragraph, block in zip(document.paragraphs, main_blocks, strict=True):
        if block["тип"] == "разрыв_сцены":
            continue
        text_runs = [run for run in paragraph.runs if not _run_contains_footnote_reference(run)]
        fragments = [fragment for fragment in block["фрагменты"] if fragment["сноска"] is None]
        if len(text_runs) != len(fragments):
            raise ValueError(f"Структура оформления блока {block['идентификатор']} изменилась.")
        for run, fragment in zip(text_runs, fragments, strict=True):
            run.text = fragment["текст"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    document.save(destination)
    _replace_footnote_text_in_package(destination, translated_blocks)
    errors = inspect_docx(destination)
    if errors:
        destination.unlink(missing_ok=True)
        raise ValueError(" ".join(errors))
```

Не очищать `paragraph.text`: это удаляет run-level оформление и ссылки на сноски. `_run_contains_footnote_reference` проверяет XPath-потомка `w:footnoteReference`. `extract_docx` создаёт в основном блоке отдельный фрагмент с пустым текстом и полем `сноска`, поэтому агент не может удалить ссылку незаметно. `_replace_footnote_text_in_package` стандартными `zipfile` и `xml.etree.ElementTree` заменяет только `w:t` соответствующих блоков `F...`, перепаковывает документ через временный ZIP и сохраняет остальные части без изменений.

- [ ] **Шаг 4. Реализовать предварительное обнаружение неподдерживаемых конструкций**

`inspect_docx` открывает ZIP и возвращает предупреждения, если присутствуют:

```python
UNSUPPORTED_PARTS = {
    "word/comments.xml": "Документ содержит комментарии.",
    "word/people.xml": "Документ содержит данные совместного редактирования.",
}
UNSUPPORTED_XML = {
    "w:ins": "Документ содержит отслеживаемые вставки.",
    "w:del": "Документ содержит отслеживаемые удаления.",
    "w:txbxContent": "Документ содержит связанные текстовые блоки.",
}
```

Повреждённый, зашифрованный или не являющийся ZIP файл даёт ошибку `Документ DOCX повреждён или защищён паролем.`. Обычные изображения и неизвестные неизменяемые ZIP-части копируются `python-docx` как часть шаблона и не считаются переводимыми блоками.

- [ ] **Шаг 5. Запустить проверки DOCX**

Выполнить: `python -m unittest tests.test_docx_blocks -v`

Ожидается: `OK`; round-trip открывается, стили и ссылка на сноску сохранены, текст сноски переведён.

- [ ] **Шаг 6. Зафиксировать обратную сборку**

```bash
git add skills/book-translator/scripts/documents.py tests/docx_fixture.py tests/test_docx_blocks.py
git commit -m "feat: собирать docx без потери оформления"
```

### Задача 6. Мост Pages и платформенные предварительные проверки

**Файлы:**

- Создать: `skills/book-translator/scripts/pages-bridge.applescript`
- Изменить: `skills/book-translator/scripts/documents.py`
- Создать: `tests/test_platform_formats.py`

**Интерфейсы:**

- Потребляет: входной путь, выходной путь, направление `export` или `import`, явный флаг разрешения внешнего приложения.
- Производит: `preflight_formats(input_suffixes: set[str], output_format: str, system: str, pages_available: bool) -> list[str]`, `preflight_python(version: tuple[int, int, int]) -> list[str]`, `preflight_dependency(version: str | None) -> list[str]`, `runtime_report() -> dict[str, str | None]` и `run_pages_bridge(mode: str, source: Path, destination: Path, allowed: bool) -> None`.
- Команда моста: `osascript pages-bridge.applescript export SOURCE DESTINATION` либо `... import SOURCE DESTINATION`.

- [ ] **Шаг 1. Написать падающие тесты матрицы платформ**

```python
# tests/test_platform_formats.py
import os
import sys
import tempfile
import unittest
from pathlib import Path


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

    def test_pages_bridge_requires_explicit_permission(self):
        with self.assertRaisesRegex(PermissionError, "разреш"):
            documents.run_pages_bridge("export", Path("a.pages"), Path("a.docx"), allowed=False)

    def test_missing_python_docx_stops_with_install_instruction(self):
        errors = documents.preflight_dependency(None)
        self.assertTrue(any("python-docx" in error and "разреш" in error for error in errors))

    def test_old_python_is_rejected(self):
        errors = documents.preflight_python((3, 9, 18))
        self.assertTrue(any("3.10" in error for error in errors))
```

- [ ] **Шаг 2. Запустить тест и подтвердить ожидаемое падение**

Выполнить: `python -m unittest tests.test_platform_formats -v`

Ожидается: отсутствие `preflight_formats`.

- [ ] **Шаг 3. Реализовать платформенную матрицу и безопасный вызов**

В `documents.py` добавить стандартные импорты `importlib.metadata`, `platform`, `subprocess` и `sys`; сеть и установка пакетов из этой функции не вызываются.

```python
def preflight_formats(input_suffixes: set[str], output_format: str,
                      system: str, pages_available: bool) -> list[str]:
    errors: list[str] = []
    wants_pages = ".pages" in input_suffixes or output_format == "pages"
    if system == "Windows" and wants_pages:
        errors.append("Windows не поддерживает Pages: используйте Mac с Pages или сохраните документ как DOCX.")
    elif system == "Darwin" and wants_pages and not pages_available:
        errors.append("Приложение Pages не найдено на этом Mac.")
    elif system not in {"Windows", "Darwin"}:
        errors.append(f"Операционная система {system} не поддерживается первой версией плагина.")
    return errors


def preflight_dependency(version: str | None) -> list[str]:
    if version is None:
        return ["Пакет python-docx не найден. Покажите команду установки и запросите разрешение пользователя."]
    match = re.match(r"^(\d+)\.(\d+)", version)
    supported = match is not None and int(match.group(1)) == 1 and int(match.group(2)) >= 2
    if not supported:
        return [f"Версия python-docx {version} не поддерживается; требуется 1.2 или новее, но ниже 2.0."]
    return []


def preflight_python(version: tuple[int, int, int]) -> list[str]:
    if version < (3, 10, 0):
        return [f"Python {version[0]}.{version[1]} не поддерживается; требуется Python 3.10 или новее."]
    return []


def runtime_report() -> dict[str, str | None]:
    try:
        version = importlib.metadata.version("python-docx")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {"python": sys.executable, "python_version": platform.python_version(),
            "python_docx": version}


def run_pages_bridge(mode: str, source: Path, destination: Path, allowed: bool) -> None:
    if not allowed:
        raise PermissionError("Для запуска Pages требуется явное разрешение пользователя.")
    if mode not in {"export", "import"}:
        raise ValueError("Направление Pages должно быть export или import.")
    script = Path(__file__).with_name("pages-bridge.applescript")
    completed = subprocess.run(
        ["osascript", str(script), mode, str(source.resolve()), str(destination.resolve())],
        text=True, capture_output=True, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Pages не смог обработать документ: {completed.stderr.strip()}")
```

- [ ] **Шаг 4. Создать AppleScript с двумя явными режимами**

```applescript
on run argv
    if (count of argv) is not 3 then error "Ожидаются режим, исходный путь и итоговый путь."
    set operationMode to item 1 of argv
    set sourcePath to POSIX file (item 2 of argv)
    set destinationPath to POSIX file (item 3 of argv)
    tell application "Pages"
        if operationMode is "export" then
            set sourceDocument to open sourcePath
            export sourceDocument to destinationPath as Microsoft Word
            close sourceDocument saving no
        else if operationMode is "import" then
            set sourceDocument to open sourcePath
            save sourceDocument in destinationPath
            close sourceDocument saving no
        else
            error "Неизвестный режим Pages: " & operationMode
        end if
    end tell
end run
```

- [ ] **Шаг 5. Добавить opt-in проверку реального Pages**

В `tests/test_platform_formats.py` добавить тест с декораторами:

```python
@unittest.skipUnless(sys.platform == "darwin", "Проверка Pages выполняется только на macOS")
@unittest.skipUnless(os.environ.get("BOOK_TRANSLATOR_TEST_PAGES") == "1",
                     "Запуск Pages требует явного BOOK_TRANSLATOR_TEST_PAGES=1")
def test_real_pages_round_trip(self):
    fixture = Path(__file__).resolve().parent / "fixtures/pages-smoke.pages"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        exported = root / "exported.docx"
        imported = root / "imported.pages"
        documents.run_pages_bridge("export", fixture, exported, allowed=True)
        documents.run_pages_bridge("import", exported, imported, allowed=True)
        self.assertGreater(exported.stat().st_size, 0)
        self.assertGreater(imported.stat().st_size, 0)
```

Фикстуру `tests/fixtures/pages-smoke.pages` создать один раз в Pages с заголовком, обычным абзацем, курсивом и полужирным фрагментом; добавить её в коммит этой задачи только после успешного локального round-trip.

- [ ] **Шаг 6. Запустить переносимые тесты**

Выполнить: `python -m unittest tests.test_platform_formats -v`

Ожидается: 6 тестов проходят; реальная проверка Pages пропущена без переменной окружения.

- [ ] **Шаг 7. На Mac с разрешением пользователя запустить реальный smoke-тест**

Выполнить: `BOOK_TRANSLATOR_TEST_PAGES=1 python -m unittest tests.test_platform_formats.PlatformFormatTests.test_real_pages_round_trip -v`

Ожидается: `OK`, Pages создаёт оба непустых файла. На Windows этот шаг отмечается как неприменимый, а не как пройденный.

- [ ] **Шаг 8. Зафиксировать Pages-мост**

```bash
git add skills/book-translator/scripts/documents.py skills/book-translator/scripts/pages-bridge.applescript tests/test_platform_formats.py tests/fixtures/pages-smoke.pages
git commit -m "feat: добавить подтверждаемый мост Pages"
```

### Задача 7. Машина этапов, контрольные точки и восстанавливаемая транзакция

**Файлы:**

- Изменить: `skills/book-translator/scripts/progress.py`
- Изменить: `tests/test_progress.py`

**Интерфейсы:**

- Потребляет: `progress.json`, `work/manifest.json`, артефакты этапов и подготовленную транзакцию.
- Производит: `start_chapter`, `advance_stage`, `prepare_transaction`, `commit_transaction`, `recover_transaction`, `check_consistency`, `finish_book`, `restart_project`.
- Порядок этапов: `извлечение → перевод → полнота_1 → проверка_1 → редактура_1 → полнота_2 → проверка_2 → [редактура_2 → проверка_3] → сборка → память → фиксация → готово`.

- [ ] **Шаг 1. Написать падающие тесты последовательности и запрета преждевременной памяти**

```python
# дополнение tests/test_progress.py
class StageMachineTests(unittest.TestCase):
    def test_stage_cannot_be_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            with self.assertRaisesRegex(ValueError, "ожидался"):
                progress.advance_stage(project, "проверка_1", artifact="report-1.json")

    def test_failed_verification_does_not_reach_memory_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            progress.advance_stage(project, "извлечение", artifact="source-blocks.json")
            progress.advance_stage(project, "перевод", artifact="draft.json")
            progress.advance_stage(project, "полнота_1", artifact="completeness-1.json")
            progress.record_failure(project, "проверка_1", "Критический пропуск")
            state = progress.load_progress(project)
            self.assertEqual("ошибка", state["статус_книги"])
            self.assertNotEqual("память", state["этап"])

    def test_active_marker_exists_until_consistent_book_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            progress.start_chapter(project, "chapter-1.docx")
            self.assertTrue((project / "work/active.json").is_file())
            with self.assertRaisesRegex(ValueError, "не заверш"):
                progress.finish_book(project)
```

- [ ] **Шаг 2. Запустить тесты машины этапов и подтвердить падение**

Выполнить: `python -m unittest tests.test_progress.StageMachineTests -v`

Ожидается: отсутствие `start_chapter`.

- [ ] **Шаг 3. Реализовать единственную таблицу допустимых переходов**

```python
STAGE_AFTER = {
    "ожидает_извлечения": "извлечение",
    "извлечение": "перевод",
    "перевод": "полнота_1",
    "полнота_1": "проверка_1",
    "проверка_1": "редактура_1",
    "редактура_1": "полнота_2",
    "полнота_2": "проверка_2",
    "проверка_2": "сборка",
    "редактура_2": "проверка_3",
    "проверка_3": "сборка",
    "сборка": "память",
    "память": "фиксация",
    "фиксация": "готово",
}


def advance_stage(project_dir: Path, completed_stage: str, artifact: str) -> None:
    state = load_progress(project_dir)
    expected = STAGE_AFTER[state["этап"]]
    if completed_stage != expected:
        raise ValueError(f"Нельзя завершить этап {completed_stage}: ожидался {expected}.")
    state["этап"] = completed_stage
    state.setdefault("артефакты", {})[completed_stage] = artifact
    write_json_atomic(project_dir / "progress.json", state)
```

`start_chapter` создаёт `work/active.json` с абсолютным путём проекта, текущей главой и временем начала. `finish_book` разрешён только при нуле необработанных глав и пустом результате `check_consistency`; затем он ставит `статус_книги = готово` и удаляет active-маркер. Ошибка сохраняет маркер, чтобы hook мог проверить объяснение.

Для дополнительного редакторского цикла отдельная функция `request_second_edit(project_dir)` разрешена только из `проверка_2`, меняет ожидаемый следующий этап на `редактура_2` и увеличивает `редакторские_циклы` до 2. Критическая ошибка после `проверка_3` вызывает `record_failure`.

- [ ] **Шаг 4. Написать падающий тест прерывания в середине фиксации**

```python
def make_file(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def make_state_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name in progress.STATE_ASSETS:
        (path / name).write_text(f"# {name}\n", encoding="utf-8")
    return path


class TransactionTests(unittest.TestCase):
    def test_recovery_finishes_ready_transaction_without_retranslation(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            transaction = progress.prepare_transaction(
                project,
                chapter_name="chapter-1.docx",
                built_document=make_file(project / "work/result.docx", b"result"),
                next_state=make_state_directory(project / "work/next-state"),
                next_progress={"версия": 1, "статус_книги": "в_работе", "этап": "готово",
                               "текущая_глава": "chapter-1.docx", "последняя_готовая_глава": "chapter-1.docx",
                               "ошибка": None},
            )
            progress.commit_transaction(project, transaction, interrupt_after="state")
            progress.recover_transaction(project)
            self.assertTrue((project / "output/chapter-1.docx").is_file())
            self.assertEqual("chapter-1.docx", progress.load_progress(project)["последняя_готовая_глава"])
            self.assertEqual([], progress.check_consistency(project))
```

- [ ] **Шаг 5. Реализовать транзакцию с маркером и резервными копиями**

`prepare_transaction` создаёт `work/transactions/<sha256-главы>/new-output`, `new-state`, `next-progress.json`, `backup`, затем проверяет все файлы и последним создаёт `готово-к-фиксации`. `commit_transaction` выполняет в фиксированном порядке:

```python
def commit_transaction(project_dir: Path, transaction_dir: Path,
                       interrupt_after: str | None = None) -> None:
    if not (transaction_dir / "готово-к-фиксации").is_file():
        raise ValueError("Транзакция не готова к фиксации.")
    _backup_current(project_dir, transaction_dir / "backup")
    _replace_directory(transaction_dir / "new-state", project_dir / "state")
    _mark_step(transaction_dir, "state")
    if interrupt_after == "state":
        return
    metadata = json.loads((transaction_dir / "transaction.json").read_text(encoding="utf-8"))
    chapter_name = metadata["глава"]
    _replace_file(transaction_dir / "new-output" / chapter_name,
                  project_dir / "output" / chapter_name)
    _mark_step(transaction_dir, "output")
    if interrupt_after == "output":
        return
    next_progress = json.loads((transaction_dir / "next-progress.json").read_text(encoding="utf-8"))
    next_progress["sha256_результата"] = _file_sha256(project_dir / "output" / chapter_name)
    next_progress["sha256_памяти"] = directory_sha256(project_dir / "state")
    write_json_atomic(transaction_dir / "next-progress.json", next_progress)
    _replace_file(transaction_dir / "next-progress.json", project_dir / "progress.json")
    _mark_step(transaction_dir, "progress")
    (transaction_dir / "завершено").write_text("да\n", encoding="utf-8")
```

`recover_transaction` находит ровно одну незавершённую готовую транзакцию и идемпотентно повторяет отсутствующие шаги. Если маркера готовности нет, она удаляет только временную транзакцию и не меняет опубликованные данные. Если данные нельзя согласовать, восстанавливает `state/`, `output/`, `progress.json` из `backup` и возвращает русскую ошибку. `check_consistency` повторно вычисляет SHA-256 опубликованного результата и всего каталога `state/`, сравнивает их с `progress.json`, проверяет завершённый маркер транзакции и возвращает список русских расхождений.

- [ ] **Шаг 6. Добавить тест безопасного явного перезапуска**

```python
    def test_restart_backs_up_results_and_never_touches_input(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            progress.initialize_project(project)
            (project / "input").mkdir()
            original = project / "input/chapter-1.docx"
            original.write_bytes(b"original")
            (project / "output/result.docx").write_bytes(b"translated")
            backup = progress.restart_project(project, confirmed=True)
            self.assertEqual(b"original", original.read_bytes())
            self.assertEqual(b"translated", (backup / "output/result.docx").read_bytes())
            self.assertEqual([], list((project / "output").iterdir()))
```

`restart_project(..., confirmed=False)` только возвращает список затрагиваемых путей и ничего не изменяет. При подтверждении резервная копия создаётся в `work/restarts/<UTC-время>/`; исходники не входят в разрешённый набор путей.

- [ ] **Шаг 7. Запустить все тесты состояния**

Выполнить: `python -m unittest tests.test_progress -v`

Ожидается: все тесты проходят, включая искусственное прерывание и восстановление.

- [ ] **Шаг 8. Зафиксировать контрольные точки**

```bash
git add skills/book-translator/scripts/progress.py tests/test_progress.py
git commit -m "feat: добавить восстанавливаемые контрольные точки"
```

### Задача 8. Четыре custom agents и подтверждаемая установка

**Файлы:**

- Создать: `agents/translator.toml`
- Создать: `agents/verifier.toml`
- Создать: `agents/editor.toml`
- Создать: `agents/state-updater.toml`
- Создать: `skills/book-translator/scripts/install-agents.py`
- Создать: `tests/test_agents.py`

**Интерфейсы:**

- Потребляет: каталог `agents/` в корне плагина и пользовательский `~/.codex/agents/`.
- Производит: четыре имена `book_translator_translator`, `book_translator_verifier`, `book_translator_editor`, `book_translator_state_updater`; `plan_install(source_dir, target_dir) -> list[dict]`; `install_agents(source_dir, target_dir, confirmed: bool, overwrite: set[str]) -> list[str]`.
- TOML намеренно не содержит `model` и `model_reasoning_effort`; каждый агент пишет только указанный координатором артефакт.

- [ ] **Шаг 1. Написать падающие структурные тесты TOML**

```python
# tests/test_agents.py
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "translator.toml": "book_translator_translator",
    "verifier.toml": "book_translator_verifier",
    "editor.toml": "book_translator_editor",
    "state-updater.toml": "book_translator_state_updater",
}


class AgentDefinitionTests(unittest.TestCase):
    def test_four_narrow_agents_have_russian_instructions_and_no_model_pin(self):
        for filename, name in EXPECTED.items():
            with self.subTest(filename=filename):
                data = tomllib.loads((ROOT / "agents" / filename).read_text(encoding="utf-8"))
                self.assertEqual(name, data["name"])
                self.assertGreater(len(data["developer_instructions"]), 300)
                self.assertNotIn("model", data)
                self.assertNotIn("model_reasoning_effort", data)
                self.assertIn("рус", (data["description"] + data["developer_instructions"]).lower())
```

- [ ] **Шаг 2. Запустить тест и подтвердить ожидаемое падение**

Выполнить: `python -m unittest tests.test_agents.AgentDefinitionTests -v`

Ожидается: отсутствие каталога `agents/`.

- [ ] **Шаг 3. Создать четыре полных TOML-контракта**

```toml
# agents/translator.toml
name = "book_translator_translator"
description = "Русскоязычный художественный переводчик английской прозы; создаёт полный черновик главы и сохраняет структуру блоков."
sandbox_mode = "workspace-write"
developer_instructions = """
Ты — профессиональный переводчик современной англоязычной художественной прозы. Работай только с путями, указанными в стартовом задании. Прочитай исходные блоки либо манифест частей главы, glossary.md, characters.md, style-guide.md, story-state.md, chapter-summaries.md и translation-principles.md. Создай русский художественный перевод так, словно текст изначально написан по-русски. Переводи смысл, голос, ритм и эффект, а не английский порядок слов. Не смягчай тяжёлые темы, не цензурируй, не добавляй объяснений и не унифицируй голоса персонажей. Если глава разделена, обработай все части последовательно в этом же запуске; перед каждой следующей частью сохрани краткое состояние сцены, незавершённые связи и небольшой хвост предыдущего оригинала и перевода. Контекстный хвост не записывай повторно. Сохрани каждый идентификатор блока ровно один раз, его порядок, число фрагментов и маркеры курсива, полужирного текста, сносок и разрывов сцен. Не изменяй память проекта. Запиши только запрошенный JSON-файл результата и кратко сообщи координатору путь.
"""
```

```toml
# agents/verifier.toml
name = "book_translator_verifier"
description = "Независимый русскоязычный проверяющий полноту, смысл, терминологию и естественность художественного перевода."
sandbox_mode = "workspace-write"
developer_instructions = """
Ты — независимый проверяющий художественного перевода с английского на русский. Работай только с файлами, указанными в стартовом задании, и не используй предположения из родительской беседы. Сопоставь оригинал и перевод по идентификаторам и соседним блокам. Найди пропуски, добавленные смыслы, ошибки отрицания, модальности, субъектов, пола, отношений, имён и терминов; отметь кальку, неестественные реплики, потерю намеренных повторов и смешение голосов. Не требуй одинакового количества предложений. Не изменяй перевод и память. Запиши русскоязычный JSON-отчёт: общий статус `пройдено`, `нужна_редактура` или `критическая_ошибка`, затем список замечаний с серьёзностью, идентификатором блока, объяснением и минимальной рекомендацией. Если замечаний нет, список остаётся пустым.
"""
```

```toml
# agents/editor.toml
name = "book_translator_editor"
description = "Русскоязычный литературный редактор; исправляет подтверждённые проблемы без смыслового обеднения."
sandbox_mode = "workspace-write"
developer_instructions = """
Ты — литературный редактор русского художественного перевода. Прочитай оригинал, текущий перевод, отчёт verifier, справочники и translation-principles.md по путям из стартового задания. Исправь все подтверждённые замечания, убери англоязычный порядок слов и проверь естественность каждой реплики. Не добавляй факты, эмоции, логические связки и красивости, которых нет в оригинале; не делай текст грубее, сентиментальнее или возвышеннее. Сохрани неоднозначность, голоса и авторскую степень тяжести тем. Сохрани каждый идентификатор блока ровно один раз, порядок, число фрагментов и форматирующие маркеры. Не обновляй память проекта. Запиши только новый JSON перевода и кратко сообщи координатору путь.
"""
```

```toml
# agents/state-updater.toml
name = "book_translator_state_updater"
description = "Русскоязычный хранитель памяти проекта; создаёт следующую полную версию справочников после принятой главы."
sandbox_mode = "workspace-write"
developer_instructions = """
Ты — хранитель памяти последовательного перевода романа. Получи оригинал, окончательно проверенный перевод, действующий каталог state и пустой каталог следующей версии по путям из стартового задания. Скопируй полную действующую память в следующую версию и обнови characters.md, glossary.md, style-guide.md, story-state.md, chapter-summaries.md и decisions.md только сведениями, подтверждёнными принятой главой. Не меняй перевод. Не превращай догадки в факты: неуверенные варианты записывай в раздел «Нерешённые вопросы» decisions.md. Сохраняй ручные пользовательские дополнения и русский язык всех записей. Не изменяй действующий state; записывай только подготовленную следующую версию.
"""
```

- [ ] **Шаг 4. Написать падающие тесты установки без молчаливой перезаписи**

```python
class AgentInstallationTests(unittest.TestCase):
    def test_installer_requires_confirmation_and_preserves_modified_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agents"
            target.mkdir()
            existing = target / "translator.toml"
            existing.write_text("изменено пользователем", encoding="utf-8")
            command = [sys.executable, str(ROOT / "skills/book-translator/scripts/install-agents.py"),
                       "--source", str(ROOT / "agents"), "--target", str(target), "--confirm"]
            completed = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(0, completed.returncode)
            self.assertEqual("изменено пользователем", existing.read_text(encoding="utf-8"))
            self.assertIn("перезапис", completed.stderr.lower())
```

- [ ] **Шаг 5. Реализовать предпросмотр и явный overwrite**

CLI:

```text
python install-agents.py --source PLUGIN/agents --target ~/.codex/agents --plan
python install-agents.py --source PLUGIN/agents --target ~/.codex/agents --confirm
python install-agents.py --source PLUGIN/agents --target ~/.codex/agents --confirm --overwrite verifier.toml
```

Без `--confirm` сценарий только печатает русскую таблицу `создать / совпадает / отличается`. При `--confirm` создаёт отсутствующие и пропускает совпадающие файлы. Отличающийся файл вызывает код 2, пока его имя явно не передано через повторяемый `--overwrite`. Копирование выполняется через временный файл и `Path.replace`.

- [ ] **Шаг 6. Запустить тесты агентов и установщика**

Выполнить: `python -m unittest tests.test_agents -v`

Ожидается: все TOML читаются, модель не закреплена, изменённый файл не перезаписан.

- [ ] **Шаг 7. Зафиксировать custom agents**

```bash
git add agents skills/book-translator/scripts/install-agents.py tests/test_agents.py
git commit -m "feat: добавить четыре изолированных агента"
```

### Задача 9. Переводческие справочники и skill-координатор

**Файлы:**

- Создать: `skills/book-translator/references/translation-principles.md`
- Создать: `skills/book-translator/references/verification-rules.md`
- Создать: `skills/book-translator/SKILL.md`
- Создать: `tests/test_skill_contract.py`

**Интерфейсы:**

- Потребляет: обычную русскую команду пользователя, пути проекта, сценарии задач 2–8 и четыре установленных agent name.
- Производит: последовательный вызов `translator → verifier → editor → новый verifier`, при необходимости один дополнительный `editor → новый verifier`, затем `state-updater` и фиксацию; длинная глава остаётся внутри одного экземпляра translator и делится только между блоками.
- Стартовое задание каждого субагента содержит только роль, абсолютные пути входов и ожидаемого выхода; история родительской беседы и вывод предыдущего агента не копируются.

- [ ] **Шаг 1. Написать падающий тест обязательных правил skill**

```python
# tests/test_skill_contract.py
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/book-translator/SKILL.md"


class SkillContractTests(unittest.TestCase):
    def test_skill_declares_russian_chat_trigger_and_defaults(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("name: book-translator", text)
        self.assertIn("текущая папка", text)
        self.assertIn("как в оригинале", text)
        self.assertIn("продолж", text.lower())

    def test_skill_requires_fresh_sequential_agents(self):
        text = SKILL.read_text(encoding="utf-8")
        required_in_order = [
            "book_translator_translator",
            "book_translator_verifier",
            "book_translator_editor",
            "book_translator_verifier",
            "book_translator_state_updater",
        ]
        positions = []
        start = 0
        for name in required_in_order:
            position = text.find(name, start)
            self.assertGreaterEqual(position, 0, name)
            positions.append(position)
            start = position + len(name)
        self.assertEqual(sorted(positions), positions)
        self.assertRegex(text, r"(?i)чист(ым|ой) контекст")
        self.assertRegex(text, r"(?i)никогда.*параллел")
        self.assertIn("не передавай историю", text.lower())

    def test_specific_book_data_is_not_in_universal_references(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "skills/book-translator/references").glob("*.md")
        ).casefold()
        for forbidden in ("илиш", "магнус", "дрейк", "кел", "джейд", "силас", "coke"):
            self.assertNotIn(forbidden, combined)

    def test_long_chapter_uses_one_translator_for_sequential_chunks(self):
        text = SKILL.read_text(encoding="utf-8").lower()
        self.assertIn("один экземпляр translator", text)
        self.assertIn("части главы последовательно", text)
        self.assertIn("контекстный хвост", text)
        self.assertIn("не записывай повторно", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Шаг 2. Запустить тест и подтвердить ожидаемое падение**

Выполнить: `python -m unittest tests.test_skill_contract -v`

Ожидается: отсутствие `SKILL.md`.

- [ ] **Шаг 3. Перенести универсальные части исходного prompt в два справочника**

`translation-principles.md` должен содержать отдельные разделы:

```markdown
# Принципы художественного перевода

## Смысл, интонация и художественный эффект
## Запрет на англоязычную кальку
## Голоса рассказчика и персонажей
## Намеренные повторы, паузы, обрывы и ритм
## Неоднозначность, тяжёлые темы и отсутствие цензуры
## Русская прямая речь и типографика
## Метафоры без смыслового обеднения
## Запрет на добавления и украшательства
## Финальная редактура естественной русской прозы
```

`verification-rules.md` должен содержать:

```markdown
# Правила проверки перевода

## Критическая ошибка
Пропуск или добавление смысла, неверное отрицание, субъект, модальность, факт, пол, отношение, имя, термин либо повреждение структуры блока.

## Требует редактуры
Калька, неестественный порядок слов, смешение голосов, потеря намеренного повтора, неуместное сглаживание или изменение авторской степени грубости.

## Пройдено
Все блоки сопоставлены, критических ошибок нет, русский текст естественен и соответствует справочникам проекта.
```

Конкретные персонажи и пользовательский глоссарий в эти файлы не переносятся. Они добавляются пользователем или `state-updater` только в `state/` проекта книги.

- [ ] **Шаг 4. Создать полный русскоязычный SKILL.md**

Начало файла:

```markdown
---
name: book-translator
description: Последовательно переводит английские главы книги на русский язык из DOCX или Pages, проверяет полноту, сохраняет оформление и умеет продолжать прерванную работу.
---

# Последовательный перевод книги

Используй этот skill, когда пользователь просит перевести книгу или набор глав с английского на русский и передаёт папку либо запускает Codex в папке книги.
```

Далее зафиксировать точный порядок действий:

```markdown
## Разбор команды

1. Возьми папку из текущего сообщения; если её нет, используй текущую папку.
2. Возьми формат `pages`, `docx` или `как в оригинале`; если его нет, прочитай `config.ini`, затем используй `как в оригинале`.
3. Если пользователь явно сказал «начать заново», покажи затрагиваемые output/state/progress, запроси подтверждение и только затем вызови подтверждённый перезапуск.
4. В остальных случаях продолжай с последней успешной контрольной точки.
5. До первого агента проверь платформу, форматы, Pages, Python, python-docx, чтение исходников, запись служебных папок, порядок глав, выходные имена, манифест и наличие четырёх custom agents.
6. Если python-docx отсутствует, покажи точную команду установки и запроси разрешение; не устанавливай пакет скрыто.
7. Если обработанный исходник изменён, предложи сохранить прежний результат либо заново перевести изменённую и все последующие главы; не выбирай за пользователя.

## Неподвижные правила координации

- Никогда не запускай главы или ролевых агентов параллельно.
- Не выполняй художественный перевод в основном контексте.
- Для каждого этапа запускай новый экземпляр указанного custom agent с чистым контекстом и не передавай историю родительской беседы.
- В стартовом задании передавай только имя роли, абсолютные пути нужных файлов и абсолютный путь ожидаемого результата.
- Дождись завершения одного агента, проверь ожидаемый файл и только после этого запускай следующего.
- Если custom agents недоступны, остановись с русской инструкцией установки; не переходи к общему контексту.

## Порядок одной главы

1. Если исходник `.pages`, после разрешения пользователя экспортируй его через Pages во временный DOCX внутри `work/`; иначе используй исходный DOCX. Извлеки блоки и зафиксируй этап `извлечение`.
2. Выбери безопасный размер части по доступному контексту модели, используя 60000 символов как начальный верхний предел без пользовательской настройки. Если глава не помещается, раздели её только между блоками, создай манифест частей и запусти один экземпляр translator, который обрабатывает части главы последовательно, ведёт краткое состояние сцены и использует контекстный хвост без повторной записи. Запусти `book_translator_translator` с чистым контекстом; проверь объединённый JSON и зафиксируй `перевод`.
3. Выполни механическую полноту и зафиксируй `полнота_1`.
4. Запусти новый `book_translator_verifier`; зафиксируй `проверка_1`.
5. Запусти новый `book_translator_editor`; зафиксируй `редактура_1`.
6. Повтори механическую полноту и зафиксируй `полнота_2`.
7. Запусти второй новый `book_translator_verifier`, не передавая ему первый отчёт; зафиксируй `проверка_2`.
8. При критической ошибке один раз запусти новый `book_translator_editor`, затем третий новый `book_translator_verifier`. При оставшейся критической ошибке остановись и сохрани отчёт.
9. Собери и открой итоговый DOCX во временной транзакции. Если выбран результат `.pages`, после разрешения пользователя импортируй проверенный DOCX через Pages и проверь созданный `.pages`. Промежуточный DOCX не публикуй как результат.
10. Запусти новый `book_translator_state_updater` для полной следующей версии памяти.
11. Подготовь и зафиксируй транзакцию; только после этого переходи к следующей главе.
```

В конце добавить шаблоны стартовых заданий. Пример для translator:

```text
Роль: художественный переводчик.
Прочитай правила: {plugin_root}/skills/book-translator/references/translation-principles.md
Прочитай исходные блоки: {chapter_work}/source-blocks.json
Прочитай память: {project}/state/
Запиши только результат: {chapter_work}/draft.json
Не используй сведения вне перечисленных файлов.
```

Для длинной главы строка исходных блоков заменяется путём `chunks-manifest.json`; один и тот же translator читает части по порядку и пишет единый `draft.json`. Координатор не запускает отдельного агента на каждую часть.

Для verifier перечислять оригинал, проверяемую версию, `verification-rules.md` и `state/`. Для первого verifier не указывать рассуждения translator; для второго не указывать `report-1.json`. Для editor указывать оригинал, текущий перевод, текущий отчёт и правила. Для state-updater указывать действующий `state/` и отдельный `transaction/new-state/`.

- [ ] **Шаг 5. Добавить в тест контракты двух редакторских циклов и остановки на критической ошибке**

```python
    def test_skill_limits_editorial_cycles_and_blocks_failed_chapter(self):
        text = SKILL.read_text(encoding="utf-8").lower()
        self.assertIn("один раз", text)
        self.assertIn("оставшейся критической ошибке", text)
        self.assertIn("остановись", text)
        self.assertIn("только после этого переходи к следующей главе", text)
```

- [ ] **Шаг 6. Запустить тесты skill**

Выполнить: `python -m unittest tests.test_skill_contract -v`

Ожидается: `OK`, 5 тестов.

- [ ] **Шаг 7. Зафиксировать skill и переводческие правила**

```bash
git add skills/book-translator/SKILL.md skills/book-translator/references tests/test_skill_contract.py
git commit -m "feat: добавить последовательный skill перевода"
```

### Задача 10. Единственный Stop-hook контроля согласованности

**Файлы:**

- Создать: `hooks/hooks.json`
- Создать: `hooks/check-progress.py`
- Создать: `tests/test_stop_hook.py`

**Интерфейсы:**

- Потребляет: JSON события `Stop` из stdin, `cwd`, `stop_hook_active`, `work/active.json`, `progress.json` и `progress.check_consistency` проекта.
- Производит: JSON в stdout. Для обычной папки — `{"continue": true, "suppressOutput": true}`; для несогласованного активного перевода — `{"decision": "block", "reason": "..."}`.
- Hook не изменяет файлы, не запускает agents и не вызывает внешние приложения.

- [ ] **Шаг 1. Написать падающие тесты трёх состояний hook**

```python
# tests/test_stop_hook.py
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks/check-progress.py"


def run_hook(cwd: Path, stop_hook_active: bool = False) -> dict:
    completed = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"cwd": str(cwd), "stop_hook_active": stop_hook_active}),
        text=True, capture_output=True, check=True,
    )
    return json.loads(completed.stdout)


class StopHookTests(unittest.TestCase):
    def test_ignores_ordinary_project(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual({"continue": True, "suppressOutput": True}, run_hook(Path(directory)))

    def test_blocks_unexplained_intermediate_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "work").mkdir()
            (project / "work/active.json").write_text("{}", encoding="utf-8")
            (project / "progress.json").write_text(json.dumps({
                "статус_книги": "в_работе", "этап": "перевод", "текущая_глава": "chapter-1.docx",
                "ошибка": None, "необработанных_глав": 1,
            }, ensure_ascii=False), encoding="utf-8")
            result = run_hook(project)
            self.assertEqual("block", result["decision"])
            self.assertIn("chapter-1.docx", result["reason"])

    def test_allows_explained_error(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "work").mkdir()
            (project / "work/active.json").write_text("{}", encoding="utf-8")
            (project / "progress.json").write_text(json.dumps({
                "статус_книги": "ошибка", "этап": "проверка_2", "текущая_глава": "chapter-1.docx",
                "ошибка": "Критический пропуск; продолжить после исправления отчёта.", "необработанных_глав": 1,
            }, ensure_ascii=False), encoding="utf-8")
            self.assertTrue(run_hook(project)["continue"])

    def test_blocks_false_completion_when_output_or_memory_hash_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "work").mkdir()
            (project / "state").mkdir()
            (project / "output").mkdir()
            (project / "work/active.json").write_text("{}", encoding="utf-8")
            (project / "progress.json").write_text(json.dumps({
                "статус_книги": "готово", "этап": "готово", "текущая_глава": "chapter-1.docx",
                "последняя_готовая_глава": "chapter-1.docx", "ошибка": None,
                "необработанных_глав": 0,
            }, ensure_ascii=False), encoding="utf-8")
            result = run_hook(project)
            self.assertEqual("block", result["decision"])
            self.assertIn("согласован", result["reason"].lower())
```

- [ ] **Шаг 2. Запустить тест и подтвердить ожидаемое падение**

Выполнить: `python -m unittest tests.test_stop_hook -v`

Ожидается: отсутствие `hooks/check-progress.py`.

- [ ] **Шаг 3. Зарегистрировать cross-platform Stop-hook**

```json
{
  "description": "Проверяет согласованность активного последовательного перевода книги",
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$PLUGIN_ROOT/hooks/check-progress.py\"",
            "command_windows": "py -3 \"%PLUGIN_ROOT%\\hooks\\check-progress.py\"",
            "timeout": 15,
            "statusMessage": "Проверяется состояние перевода книги"
          }
        ]
      }
    ]
  }
}
```

- [ ] **Шаг 4. Реализовать read-only проверку без рекурсивного блокирования**

```python
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(PLUGIN_ROOT / "skills/book-translator/scripts"))
from progress import check_consistency


ALLOW = {"continue": True, "suppressOutput": True}


def find_project(start: Path) -> Path | None:
    for candidate in (start.resolve(), *start.resolve().parents):
        if (candidate / "work/active.json").is_file():
            return candidate
    return None


def evaluate(event: dict) -> dict:
    project = find_project(Path(event.get("cwd") or Path.cwd()))
    if project is None:
        return ALLOW
    progress_path = project / "progress.json"
    if not progress_path.is_file():
        return {"decision": "block", "reason": "Активный перевод не имеет progress.json."}
    state = json.loads(progress_path.read_text(encoding="utf-8"))
    if state.get("статус_книги") == "ошибка" and state.get("ошибка"):
        return ALLOW
    if state.get("статус_книги") == "готово" and state.get("необработанных_глав", 0) == 0:
        errors = check_consistency(project)
        if errors:
            return {"decision": "block", "reason": "Завершение не согласовано: " + " ".join(errors)}
        return ALLOW
    chapter = state.get("текущая_глава") or "неизвестная глава"
    stage = state.get("этап") or "неизвестный этап"
    reason = (f"Перевод нельзя завершить: {chapter} остановлена на этапе {stage}. "
              "Продолжи обязательный этап либо запиши объяснённую критическую ошибку.")
    return {"decision": "block", "reason": reason}


if __name__ == "__main__":
    event = json.load(sys.stdin)
    json.dump(evaluate(event), sys.stdout, ensure_ascii=False)
```

Поле `stop_hook_active` не используется для безусловного разрешения: повторный вызов должен снова проверить фактическое состояние. Бесконечного цикла нет, потому что объяснённая ошибка и согласованное завершение разрешают остановку.

- [ ] **Шаг 5. Запустить тесты hook и проверить JSON-синтаксис**

Выполнить: `python -m unittest tests.test_stop_hook -v`

Выполнить: `python -m json.tool hooks/hooks.json`

Ожидается: 4 теста проходят, JSON печатается без ошибки.

- [ ] **Шаг 6. Зафиксировать hook**

```bash
git add hooks tests/test_stop_hook.py
git commit -m "feat: проверять завершение активного перевода"
```

### Задача 11. Интеграционный прогон, чистый контекст и пользовательская документация

**Файлы:**

- Создать: `tests/test_pipeline_integration.py`
- Создать: `tests/test_russian_content.py`
- Создать: `tests/acceptance/clean-context.md`
- Создать: `tests/acceptance/macos-pages.md`
- Создать: `tests/acceptance/windows-docx.md`
- Изменить: `README.md`

**Интерфейсы:**

- Потребляет: весь плагин, временный проект с тремя главами, локальную установку Codex; Pages только в macOS-приёмке.
- Производит: переносимый end-to-end тест детерминированных частей, доказательство чистого контекста реального custom agent, платформенные протоколы и русскую инструкцию пользователя.

- [ ] **Шаг 1. Написать переносимый интеграционный тест без вызова модели**

```python
# tests/test_pipeline_integration.py
import copy
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/book-translator/scripts"
sys.path.insert(0, str(SCRIPTS))
import documents
import progress


def write_json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def advance_successful_fixture_pipeline(project: Path, chapter: Path, source: list[dict],
                                        translated: list[dict], remaining: int,
                                        interrupt: bool) -> None:
    chapter_work = project / "work" / chapter.stem
    chapter_work.mkdir(parents=True, exist_ok=True)
    source_path = chapter_work / "source.json"
    draft_path = chapter_work / "draft.json"
    edited_path = chapter_work / "edited.json"
    write_json(source_path, source)
    progress.advance_stage(project, "извлечение", str(source_path))
    write_json(draft_path, translated)
    progress.advance_stage(project, "перевод", str(draft_path))
    write_json(chapter_work / "completeness-1.json", {"ошибки": []})
    progress.advance_stage(project, "полнота_1", str(chapter_work / "completeness-1.json"))
    write_json(chapter_work / "report-1.json", {"статус": "пройдено", "замечания": []})
    progress.advance_stage(project, "проверка_1", str(chapter_work / "report-1.json"))
    write_json(edited_path, translated)
    progress.advance_stage(project, "редактура_1", str(edited_path))
    write_json(chapter_work / "completeness-2.json", {"ошибки": []})
    progress.advance_stage(project, "полнота_2", str(chapter_work / "completeness-2.json"))
    write_json(chapter_work / "report-2.json", {"статус": "пройдено", "замечания": []})
    progress.advance_stage(project, "проверка_2", str(chapter_work / "report-2.json"))
    built = chapter_work / chapter.name
    documents.rebuild_docx(chapter, translated, built)
    progress.advance_stage(project, "сборка", str(built))
    next_state = chapter_work / "next-state"
    shutil.copytree(project / "state", next_state)
    with (next_state / "chapter-summaries.md").open("a", encoding="utf-8") as stream:
        stream.write(f"\n## {chapter.name}\nПринята тестовая глава.\n")
    progress.advance_stage(project, "память", str(next_state))
    progress.advance_stage(project, "фиксация", "подготовлена транзакция")
    next_progress = progress.load_progress(project)
    next_progress.update({
        "статус_книги": "в_работе" if remaining else "готово",
        "этап": "готово",
        "последняя_готовая_глава": chapter.name,
        "необработанных_глав": remaining,
        "ошибка": None,
    })
    transaction = progress.prepare_transaction(project, chapter.name, built, next_state, next_progress)
    progress.commit_transaction(project, transaction, interrupt_after="state" if interrupt else None)


class PipelineIntegrationTests(unittest.TestCase):
    def test_three_chapters_publish_in_order_and_resume_after_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            input_dir = project / "input"
            input_dir.mkdir()
            for number in (1, 2, 10):
                document = Document()
                document.add_paragraph(f"Chapter {number}")
                document.save(input_dir / f"chapter-{number}.docx")
            progress.initialize_project(project)
            chapters = documents.discover_chapters(project)
            self.assertEqual(["chapter-1.docx", "chapter-2.docx", "chapter-10.docx"],
                             [chapter.name for chapter in chapters])
            manifest = documents.build_manifest(project, chapters)
            for index, chapter in enumerate(chapters):
                progress.start_chapter(project, chapter.name)
                source = documents.extract_docx(chapter, project / f"work/{chapter.stem}/source.json")
                translated = copy.deepcopy(source)
                translated[0]["фрагменты"][0]["текст"] = f"Глава {index + 1}"
                self.assertEqual([], documents.validate_translation(source, translated))
                # Прогнать все детерминированные переходы; модельные артефакты заменены валидными фикстурами.
                advance_successful_fixture_pipeline(project, chapter, source, translated,
                                                    remaining=len(chapters) - index - 1,
                                                    interrupt=(index == 1))
                if index == 1:
                    progress.recover_transaction(project)
            progress.finish_book(project)
            self.assertEqual(["chapter-1.docx", "chapter-2.docx", "chapter-10.docx"],
                             sorted((path.name for path in (project / "output").glob("*.docx")),
                                    key=lambda name: documents.natural_key(Path(name))))
            self.assertEqual("chapter-10.docx", progress.load_progress(project)["последняя_готовая_глава"])
```

Вместо модели helper создаёт валидные отчёты `{"статус": "пройдено", "замечания": []}`. Прерывание происходит после замены `state/`, чтобы проверить реальное восстановление задачи 7.

- [ ] **Шаг 2. Запустить интеграционный тест и исправить только выявленные разрывы контрактов**

Выполнить: `python -m unittest tests.test_pipeline_integration -v`

Ожидается: `OK`, три результата опубликованы в естественном порядке, глава 2 восстановлена без повторного перевода.

- [ ] **Шаг 3. Добавить проверку русского языка человекочитаемых файлов**

```python
# tests/test_russian_content.py
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


class RussianContentTests(unittest.TestCase):
    def test_markdown_templates_references_and_skill_contain_russian(self):
        files = [ROOT / "README.md", ROOT / "skills/book-translator/SKILL.md"]
        files += list((ROOT / "skills/book-translator/assets").glob("*.md"))
        files += list((ROOT / "skills/book-translator/references").glob("*.md"))
        for path in files:
            with self.subTest(path=path):
                self.assertRegex(path.read_text(encoding="utf-8"), CYRILLIC)

    def test_agent_descriptions_and_instructions_contain_russian(self):
        for path in (ROOT / "agents").glob("*.toml"):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertRegex(data["description"], CYRILLIC)
            self.assertRegex(data["developer_instructions"], CYRILLIC)
```

- [ ] **Шаг 4. Создать воспроизводимую проверку чистого контекста**

`tests/acceptance/clean-context.md` содержит точный протокол:

```markdown
# Приёмка чистого контекста custom agent

1. Установить agents через `install-agents.py`, открыть новую задачу Codex и локально подключить плагин.
2. В родительском чате написать контрольную фразу `КОНТЕКСТ-НЕ-ДЛЯ-АГЕНТА-74931`, но не помещать её ни в один файл проекта.
3. Попросить `$book-translator` обработать одну тестовую главу и перед запуском translator добавить диагностический вопрос: «Если контрольная фраза есть в доступных файлах, запиши её в work/context-check.txt; иначе запиши НЕТ».
4. Убедиться, что custom agent запущен новым субагентом с чистым контекстом и `work/context-check.txt` содержит ровно `НЕТ`.
5. Удалить диагностический файл и повторить обычный запуск без диагностического вопроса.

Критерий: фраза из истории родительского чата не появляется ни в артефактах агента, ни в переводе.
```

Эту приёмку нельзя подменять тестом текста SKILL.md: требуется реальный запуск после регистрации custom agents. Результат запуска записать внизу файла с датой, версией Codex и `ПРОЙДЕНО` либо фактической ошибкой.

- [ ] **Шаг 5. Создать платформенные протоколы**

`macos-pages.md` проверяет три главы `.pages → .pages`, заголовок, абзацы, курсив, полужирный текст, сцену и сноску, прерывание после второй главы и продолжение. `windows-docx.md` проверяет тот же набор для `.docx → .docx` и отдельный отказ `.pages` с русской инструкцией. В каждом пункте есть поле `Результат:`, которое заполняется только после фактического запуска.

- [ ] **Шаг 6. Полностью переписать README по-русски**

README содержит разделы:

```markdown
# Переводчик художественных книг для Codex

## Возможности
## Требования
## Локальная установка плагина
## Однократная установка custom agents
## Подготовка папки книги
## Запуск из чата
## Выбор DOCX или Pages
## Продолжение после прерывания
## Явный перезапуск и резервная копия
## Что сохраняется в оформлении
## Ограничения первой версии
## Проверки для разработчиков
## Безопасность исходников
```

Примеры вызова дословно:

```text
$book-translator Переведи книгу в текущей папке.
$book-translator Переведи главы из /Книги/Роман, результат — pages.
$book-translator Продолжи перевод, выходной формат — docx.
```

README прямо сообщает: Pages нужен только на Mac; обычный ChatGPT без локального Codex и custom agents не поддерживает полный конвейер; hook нужно просмотреть и разрешить; зависимости и Pages не запускаются скрыто.

- [ ] **Шаг 7. Запустить полный переносимый набор проверок**

Выполнить: `python -m unittest discover -s tests -p "test_*.py" -v`

Выполнить: `python -m json.tool .codex-plugin/plugin.json`

Выполнить: `python -m json.tool hooks/hooks.json`

Выполнить: `git diff --check`

Ожидается: все переносимые тесты проходят; Pages-тест пропущен без явной переменной; оба JSON валидны; `git diff --check` не выводит ошибок.

- [ ] **Шаг 8. Выполнить реальную приёмку Codex и платформ**

На Mac выполнить `tests/acceptance/clean-context.md` и `tests/acceptance/macos-pages.md`. На Windows выполнить `tests/acceptance/clean-context.md` и `tests/acceptance/windows-docx.md`. Не ставить отметку готовности первой версии, пока чистый контекст, `.pages → .pages` на Mac и `.docx → .docx` на обеих платформах не имеют фактического результата `ПРОЙДЕНО`.

- [ ] **Шаг 9. Зафиксировать интеграцию и документацию**

```bash
git add README.md tests/test_pipeline_integration.py tests/test_russian_content.py tests/acceptance
git commit -m "test: подтвердить полный последовательный конвейер"
```

## Итоговая проверка готовности

- [ ] Локальный плагин обнаруживает `book-translator` и показывает русское описание.
- [ ] Установщик регистрирует четыре custom agents и не перезаписывает изменённые файлы без отдельного разрешения.
- [ ] Все главы фиксируются в манифесте и обрабатываются строго по одной.
- [ ] Каждый ролевой этап запускает новый custom agent с чистым контекстом.
- [ ] Механический валидатор блокирует пропущенный, пустой, повторённый, переставленный и неизвестный блок.
- [ ] Второй verifier не получает первый отчёт; число редакторских циклов ограничено двумя.
- [ ] `.docx` round-trip сохраняет утверждённое оформление и сноски.
- [ ] `.pages` проходит через Pages только на Mac и только после разрешения.
- [ ] Прерванная готовая транзакция восстанавливается без повторного перевода.
- [ ] Память не меняется после неуспешной проверки.
- [ ] `Stop`-hook игнорирует обычные проекты и блокирует только несогласованное активное завершение.
- [ ] Все пользовательские инструкции, сообщения и шаблоны русскоязычны с оговорёнными техническими исключениями.
- [ ] Исходные документы не изменяются ни обычным запуском, ни перезапуском.
- [ ] Полный `unittest` проходит на Windows и macOS; платформенные приёмки имеют сохранённые фактические результаты.
