from __future__ import annotations

import codecs
import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable


SUPPORTED_SUFFIXES = {".rtf"}
EXCLUDED_DIRECTORIES = {"output", "state", "work", ".codex"}
UNSAFE_DESTINATIONS = {
    "field", "header", "headerl", "headerr", "headerf",
    "footer", "footerl", "footerr", "footerf",
    "object", "shptxt", "txbx",
}
UNSAFE_CONTROLS = {"trowd", "cell", "row", "nesttableprops"}
SKIPPED_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "filetbl",
    "listtable", "listoverridetable", "generator", "xmlnstbl", "datastore",
    "themedata", "colorschememapping", "latentstyles", "rsidtbl", "fldinst",
    "atrfstart", "atrfend", "atnid", "atnauthor", "annotation", "atnref",
}
DESTINATION_WORDS = SKIPPED_DESTINATIONS | UNSAFE_DESTINATIONS | {"footnote"}


@dataclass(frozen=True)
class Token:
    kind: str
    raw: str
    start: int
    end: int
    word: str | None = None
    parameter: int | None = None


@dataclass
class Group:
    start: int
    end: int = 0
    destination: str | None = None
    parameter: int | None = None
    tokens: list[Token] = field(default_factory=list)


@dataclass
class FormatState:
    destination: str = "body"
    bold: bool = False
    italic: bool = False
    outline: int | None = None
    codepage: int = 1252
    uc: int = 1
    skip_fallback: int = 0


@dataclass
class Atom:
    text: str
    start: int
    end: int
    bold: bool
    italic: bool
    destination: str


def tokenize_rtf(raw: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "{":
            tokens.append(Token("group_start", char, index, index + 1))
            index += 1
        elif char == "}":
            tokens.append(Token("group_end", char, index, index + 1))
            index += 1
        elif char == "\\":
            start = index
            index += 1
            if index >= len(raw):
                raise ValueError("RTF оканчивается незавершенным управляющим символом.")
            marker = raw[index]
            if marker == "'":
                if index + 2 >= len(raw) or not re.fullmatch(r"[0-9a-fA-F]{2}", raw[index + 1:index + 3]):
                    raise ValueError("В RTF найден некорректный шестнадцатеричный символ.")
                index += 3
                tokens.append(Token("hex", raw[start:index], start, index))
            elif marker.isalpha():
                match = re.match(r"([A-Za-z]+)(-?\d+)? ?", raw[index:])
                if match is None:
                    raise ValueError("В RTF найден некорректный управляющий код.")
                index += len(match.group(0))
                parameter = int(match.group(2)) if match.group(2) is not None else None
                word = match.group(1)
                tokens.append(Token("control", raw[start:index], start, index, word, parameter))
                if word == "bin":
                    if parameter is None or parameter < 0 or index + parameter > len(raw):
                        raise ValueError("В RTF найден некорректный бинарный блок.")
                    tokens.append(Token("binary", raw[index:index + parameter], index, index + parameter))
                    index += parameter
            else:
                index += 1
                tokens.append(Token("symbol", raw[start:index], start, index, marker))
        else:
            start = index
            while index < len(raw) and raw[index] not in "{}\\":
                index += 1
            tokens.append(Token("text", raw[start:index], start, index))
    return tokens


def _group_index(tokens: list[Token]) -> list[Group]:
    stack: list[Group] = []
    groups: list[Group] = []
    for token in tokens:
        if token.kind == "group_start":
            stack.append(Group(token.start))
            continue
        if not stack:
            if token.kind == "text" and not token.raw.strip():
                continue
            raise ValueError("RTF содержит данные вне корневой группы.")
        stack[-1].tokens.append(token)
        if token.kind == "control" and stack[-1].destination is None and token.word in DESTINATION_WORDS:
            stack[-1].destination = token.word
            stack[-1].parameter = token.parameter
        if token.kind == "group_end":
            group = stack.pop()
            group.end = token.end
            groups.append(group)
    if stack:
        raise ValueError("RTF содержит незакрытую группу.")
    roots = [group for group in groups if group.start == 0]
    if len(roots) != 1:
        raise ValueError("RTF должен содержать одну корневую группу.")
    return groups


def _decode_hex(raw: str, codepage: int) -> str:
    value = bytes([int(raw[-2:], 16)])
    try:
        return value.decode(f"cp{codepage}")
    except (LookupError, UnicodeDecodeError):
        return value.decode("cp1252", errors="replace")


def _decode_plain_segments(raw: str, codepage: int) -> list[tuple[str, int, int]]:
    try: decoder = codecs.getincrementaldecoder(f"cp{codepage}")(errors="replace")
    except LookupError: decoder = codecs.getincrementaldecoder("cp1252")(errors="replace")
    data = raw.encode("latin-1")
    result: list[tuple[str, int, int]] = []
    sequence_start = 0
    for index, byte in enumerate(data):
        decoded = decoder.decode(bytes([byte]), final=False)
        if decoded:
            result.extend((character, sequence_start, index + 1) for character in decoded)
            sequence_start = index + 1
    tail = decoder.decode(b"", final=True)
    result.extend((character, sequence_start, len(data)) for character in tail)
    return result


def _unicode(parameter: int | None) -> str:
    if parameter is None:
        return ""
    value = parameter if parameter >= 0 else parameter + 65536
    return chr(value)


def _symbol_text(word: str | None) -> str:
    return {"~": "\u00a0", "-": "\u00ad", "_": "\u2011", "\\": "\\", "{": "{", "}": "}"}.get(word, "")


def _control_text(word: str | None) -> str:
    return {
        "par": "\n", "line": "\n", "tab": "\t", "emdash": "\u2014",
        "endash": "\u2013", "bullet": "\u2022", "lquote": "\u2018",
        "rquote": "\u2019", "ldblquote": "\u201c", "rdblquote": "\u201d",
    }.get(word, "")


def _parse_atoms(raw: str, tokens: list[Token]) -> tuple[list[Atom], list[dict]]:
    states = [FormatState()]
    atoms: list[Atom] = []
    blocks: list[dict] = []
    paragraphs: dict[str, list[Atom]] = {"body": [], "footnote": []}

    def finish(destination: str) -> None:
        paragraph = paragraphs[destination]
        text = "".join(atom.text for atom in paragraph)
        if text or paragraph:
            outline = states[-1].outline
            kind = "сноска" if destination == "footnote" else "заголовок" if outline is not None else ("разрыв_сцены" if text.strip() in {"***", "* * *", "— — —"} else "абзац")
            blocks.append({
                "номер": len(blocks) + 1,
                "тип": kind,
                "текст": text,
                "фрагменты": _fragments(paragraph),
                "исходные_диапазоны": [[atom.start, atom.end] for atom in paragraph],
            })
        paragraphs[destination] = []

    for token in tokens:
        if token.kind == "group_start":
            states.append(replace(states[-1]))
            continue
        if token.kind == "group_end":
            if len(states) == 1:
                raise ValueError("RTF содержит лишнюю закрывающую скобку.")
            if states[-1].destination == "footnote" and paragraphs["footnote"]:
                finish("footnote")
            states.pop()
            continue
        state = states[-1]
        if token.kind == "symbol" and token.word == "*":
            state.destination = "ignorable"
            continue
        if token.kind == "control":
            if token.word in DESTINATION_WORDS:
                state.destination = token.word or state.destination
                continue
            if token.word == "ansicpg" and token.parameter:
                state.codepage = token.parameter
            elif token.word == "uc" and token.parameter is not None:
                state.uc = max(0, token.parameter)
            elif token.word == "u":
                if state.destination in {"body", "footnote"}:
                    atom = Atom(_unicode(token.parameter), token.start, token.end, state.bold, state.italic, state.destination)
                    atoms.append(atom); paragraphs[state.destination].append(atom)
                state.skip_fallback = state.uc
            elif token.word in {"b", "i"}:
                setattr(state, "bold" if token.word == "b" else "italic", token.parameter != 0)
            elif token.word == "plain":
                state.bold = state.italic = False
            elif token.word == "outlinelevel":
                state.outline = token.parameter
            elif token.word == "par" and state.destination in {"body", "footnote"}:
                finish(state.destination)
            else:
                value = _control_text(token.word)
                if value and state.destination in {"body", "footnote"}:
                    atom = Atom(value, token.start, token.end, state.bold, state.italic, state.destination)
                    atoms.append(atom); paragraphs[state.destination].append(atom)
            continue
        if state.destination not in {"body", "footnote"}:
            continue
        if token.kind == "text":
            segments = _decode_plain_segments(token.raw, state.codepage)
            dropped_segments = segments[:state.skip_fallback]
            if dropped_segments and atoms: atoms[-1].end = token.start + dropped_segments[-1][2]
            state.skip_fallback = max(0, state.skip_fallback - len(segments))
            for character, local_start, local_end in segments[len(dropped_segments):]:
                if character in "\r\n": continue
                atom = Atom(character, token.start + local_start, token.start + local_end, state.bold, state.italic, state.destination)
                atoms.append(atom); paragraphs[state.destination].append(atom)
            continue
        value = _decode_hex(token.raw, state.codepage) if token.kind == "hex" else _symbol_text(token.word)
        dropped = 0
        if state.skip_fallback:
            dropped = min(len(value), state.skip_fallback)
            value = value[dropped:]
            state.skip_fallback -= dropped
            if dropped and atoms:
                atoms[-1].end = token.start + dropped if token.kind == "text" else token.end
        for character in value:
            atom = Atom(character, token.start, token.end, state.bold, state.italic, state.destination)
            atoms.append(atom); paragraphs[state.destination].append(atom)
    for destination in ("body", "footnote"):
        if paragraphs[destination]: finish(destination)
    return atoms, blocks


def _fragments(atoms: list[Atom]) -> list[dict]:
    fragments: list[dict] = []
    for atom in atoms:
        if fragments and fragments[-1]["полужирный"] == atom.bold and fragments[-1]["курсив"] == atom.italic:
            fragments[-1]["текст"] += atom.text
        else:
            fragments.append({"текст": atom.text, "полужирный": atom.bold, "курсив": atom.italic})
    return fragments


def _group_text(group: Group, codepage: int = 1252) -> str:
    result: list[str] = []
    skip = 0
    uc = 1
    for token in group.tokens:
        if token.kind == "control" and token.word == "uc" and token.parameter is not None:
            uc = max(0, token.parameter)
        elif token.kind == "control" and token.word == "u":
            result.append(_unicode(token.parameter)); skip = uc
        elif token.kind == "hex":
            value = _decode_hex(token.raw, codepage)
            if skip: skip -= 1
            else: result.append(value)
        elif token.kind == "text":
            value = "".join(character for character, _, _ in _decode_plain_segments(token.raw, codepage) if character not in "\r\n")
            if skip:
                value = value[skip:]; skip = 0
            result.append(value)
        elif token.kind == "symbol":
            value = _symbol_text(token.word)
            if skip: skip -= 1
            else: result.append(value)
        elif token.kind == "control":
            result.append(_control_text(token.word))
    return "".join(result).strip()


def extract_annotations(path_or_raw: Path | str) -> list[dict]:
    raw = path_or_raw.read_text(encoding="latin-1") if isinstance(path_or_raw, Path) else path_or_raw
    tokens = tokenize_rtf(raw)
    groups = _group_index(tokens)
    starts: dict[str, int] = {}
    ends: dict[str, int] = {}
    authors: list[str] = []
    identifiers: list[str] = []
    references: list[str] = []
    notes: list[str] = []
    for group in sorted(groups, key=lambda item: item.start):
        text = _group_text(group)
        if group.destination == "atrfstart": starts[text] = group.end
        elif group.destination == "atrfend": ends[text] = group.start
        elif group.destination == "atnauthor": authors.append(text)
        elif group.destination == "atnid": identifiers.append(text)
        elif group.destination == "atnref": references.append(str(group.parameter) if group.parameter is not None else text)
        elif group.destination == "annotation": notes.append(text)
    count = max(len(notes), len(identifiers), len(references), 0)
    result = []
    for index in range(count):
        reference = references[index] if index < len(references) else (identifiers[index] if index < len(identifiers) else str(index + 1))
        identifier = identifiers[index] if index < len(identifiers) else reference
        start, end = starts.get(reference), ends.get(reference)
        quote = _plain_range(raw, start, end) if start is not None and end is not None and start <= end else ""
        result.append({
            "id": identifier,
            "ссылка": reference,
            "автор": authors[index] if index < len(authors) else "Пользователь",
            "текст": notes[index] if index < len(notes) else "",
            "цитата": quote,
            "начало": start,
            "конец": end,
        })
    return result


def _plain_range(raw: str, start: int, end: int) -> str:
    atoms, _ = _parse_atoms(raw, tokenize_rtf(raw))
    return "".join(atom.text for atom in atoms if atom.start >= start and atom.end <= end).strip()


def inspect_rtf(path: Path) -> list[str]:
    if path.suffix.casefold() != ".rtf":
        return ["Поддерживается только формат .rtf."]
    try:
        raw = path.read_text(encoding="latin-1")
        tokens = tokenize_rtf(raw)
        groups = _group_index(tokens)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        return [f"RTF нельзя безопасно разобрать: {error}"]
    if not re.match(r"^\{\\rtf1(?:\D|$)", raw):
        return ["Файл не является RTF версии 1."]
    unsafe = sorted({group.destination for group in groups if group.destination in UNSAFE_DESTINATIONS})
    unsafe.extend(sorted({token.word for token in tokens if token.kind == "control" and token.word in UNSAFE_CONTROLS}))
    return [f"RTF содержит неподдерживаемую текстовую конструкцию: {name}." for name in sorted(set(unsafe))]


def extract_rtf(path: Path) -> dict:
    errors = inspect_rtf(path)
    if errors:
        raise ValueError(" ".join(errors))
    raw = path.read_text(encoding="latin-1")
    _, blocks = _parse_atoms(raw, tokenize_rtf(raw))
    return {
        "формат": "rtf",
        "исходный_файл": path.name,
        "блоки": blocks,
        "аннотации": extract_annotations(raw),
    }


def validate_publishable_rtf(path: Path) -> list[str]:
    errors = inspect_rtf(path)
    if errors:
        return errors
    forbidden = []
    for block in extract_rtf(path)["блоки"]:
        if "ё" in block["текст"] or "Ё" in block["текст"]:
            forbidden.append(f"Блок {block['номер']} содержит запрещенную букву ё или Ё.")
    return forbidden


def split_blocks(path: Path) -> list[dict]:
    return extract_rtf(path)["блоки"]


def _escape_text(text: str) -> str:
    result: list[str] = []
    for character in text:
        if character in "{}\\":
            result.append("\\" + character)
        elif character == "\n":
            result.append("\\line ")
        elif character == "\t":
            result.append("\\tab ")
        elif 32 <= ord(character) <= 126:
            result.append(character)
        else:
            value = ord(character)
            if value > 32767: value -= 65536
            result.append(f"\\u{value}?")
    return "".join(result)


def _render_fragments(block: dict) -> str:
    fragments = block.get("фрагменты")
    if not isinstance(fragments, list):
        fragments = [{"текст": block.get("текст", ""), "полужирный": False, "курсив": False}]
    rendered = ["{\\plain "]
    bold = italic = False
    for fragment in fragments:
        next_bold = bool(fragment.get("полужирный")); next_italic = bool(fragment.get("курсив"))
        if next_bold != bold: rendered.append("\\b " if next_bold else "\\b0 "); bold = next_bold
        if next_italic != italic: rendered.append("\\i " if next_italic else "\\i0 "); italic = next_italic
        rendered.append(_escape_text(str(fragment.get("текст", ""))))
    rendered.append("}")
    return "".join(rendered)


def rebuild_rtf(source: Path, translated: dict | list[dict], destination: Path) -> None:
    errors = inspect_rtf(source)
    if errors: raise ValueError(" ".join(errors))
    original = extract_rtf(source)
    blocks = translated.get("блоки") if isinstance(translated, dict) else translated
    if not isinstance(blocks, list) or len(blocks) != len(original["блоки"]):
        raise ValueError("Количество блоков перевода не совпадает с оригиналом.")
    replacements: list[tuple[int, int, str]] = []
    for source_block, target_block in zip(original["блоки"], blocks, strict=True):
        source_formats = [(item["полужирный"], item["курсив"]) for item in source_block["фрагменты"]]
        target_fragments = target_block.get("фрагменты") if isinstance(target_block, dict) else None
        if not isinstance(target_fragments, list):
            raise ValueError("Каждый блок перевода должен содержать фрагменты форматирования.")
        target_formats = [(bool(item.get("полужирный")), bool(item.get("курсив"))) for item in target_fragments]
        if source_formats != target_formats:
            raise ValueError("Перевод изменяет количество или типы форматирующих фрагментов.")
        spans = source_block["исходные_диапазоны"]
        if not spans:
            continue
        first_start = spans[0][0]
        replacements.append((first_start, spans[0][1], _render_fragments(target_block)))
        replacements.extend((start, end, "") for start, end in spans[1:])
    raw = source.read_text(encoding="latin-1")
    for start, end, value in sorted(replacements, reverse=True):
        raw = raw[:start] + value + raw[end:]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp.rtf")
    temporary.write_text(raw, encoding="latin-1")
    if inspect_rtf(temporary):
        temporary.unlink(missing_ok=True)
        raise ValueError("Собранный RTF не прошел механическую проверку.")
    temporary.replace(destination)


def _annotation_id(issue: dict, index: int) -> str:
    explicit = issue.get("id")
    if isinstance(explicit, str) and explicit.strip():
        return re.sub(r"[^A-Za-z0-9_-]", "-", explicit.strip())[:64]
    stable = json.dumps(issue, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "bt-" + hashlib.sha256(stable).hexdigest()[:16] + f"-{index}"


def chat_feedback_to_issue(quote: str, message: str, block: int, occurrence: int = 1) -> dict:
    if not quote.strip() or not message.strip():
        raise ValueError("Замечание из чата требует выделенной цитаты и описания.")
    seed = json.dumps([quote, message, block, occurrence], ensure_ascii=False).encode("utf-8")
    return {
        "id": "chat-" + hashlib.sha256(seed).hexdigest()[:16],
        "блок": block,
        "точная_цитата": quote,
        "номер_вхождения": occurrence,
        "серьезность": "пользовательская",
        "объяснение": message,
        "минимальная_рекомендация": "Учесть формулировку пользователя.",
    }


def _issue_positions(raw: str, issue: dict) -> tuple[int, int]:
    quote = issue.get("точная_цитата") or issue.get("цитата")
    occurrence = issue.get("номер_вхождения", 1)
    if not isinstance(quote, str) or not quote:
        raise ValueError("Для аннотации требуется точная цитата.")
    plain_atoms, blocks = _parse_atoms(raw, tokenize_rtf(raw))
    selected_atoms = plain_atoms
    block_number = issue.get("блок")
    if block_number is not None:
        try:
            block_number = int(block_number)
        except (TypeError, ValueError) as error:
            raise ValueError("Номер блока аннотации должен быть целым числом.") from error
        if not 1 <= block_number <= len(blocks):
            raise ValueError(f"Блок аннотации не найден: {block_number}.")
        ranges = {tuple(value) for value in blocks[block_number - 1]["исходные_диапазоны"]}
        selected_atoms = [atom for atom in plain_atoms if (atom.start, atom.end) in ranges]
    plain = "".join(atom.text for atom in selected_atoms)
    try: occurrence = int(occurrence)
    except (TypeError, ValueError): occurrence = 1
    cursor = -1
    for _ in range(max(1, occurrence)):
        cursor = plain.find(quote, cursor + 1)
        if cursor < 0: raise ValueError(f"Цитата для аннотации не найдена: {quote!r}.")
    selected = selected_atoms[cursor:cursor + len(quote)]
    if not selected or "".join(atom.text for atom in selected) != quote:
        raise ValueError("Цитату нельзя однозначно привязать к RTF.")
    return selected[0].start, selected[-1].end


def add_annotations(source: Path, issues: Iterable[dict], destination: Path | None = None) -> Path:
    raw = source.read_text(encoding="latin-1")
    additions: list[tuple[int, str]] = []
    for index, issue in enumerate(issues, start=1):
        start, end = _issue_positions(raw, issue)
        identifier = _annotation_id(issue, index)
        reference = int(hashlib.sha256(identifier.encode()).hexdigest()[:7], 16)
        explanation = str(issue.get("объяснение") or issue.get("текст") or "Замечание")
        recommendation = str(issue.get("минимальная_рекомендация") or issue.get("рекомендация") or "")
        severity = str(issue.get("серьезность") or issue.get("severity") or "редакторское")
        note = f"[{identifier}] {severity}: {explanation}" + (f" Рекомендация: {recommendation}" if recommendation else "")
        additions.append((start, f"{{\\*\\atrfstart {reference}}}"))
        additions.append((end, f"{{\\*\\atrfend {reference}}}{{\\*\\atnid {_escape_text(identifier)}}}{{\\*\\atnauthor Book Translator}}{{\\annotation {{\\*\\atnref{reference}}}{_escape_text(note)}}}"))
    for position, value in sorted(additions, key=lambda item: item[0], reverse=True):
        raw = raw[:position] + value + raw[position:]
    target = destination or source
    temporary = target.with_name(target.name + ".tmp.rtf")
    temporary.write_text(raw, encoding="latin-1")
    if inspect_rtf(temporary):
        temporary.unlink(missing_ok=True); raise ValueError("RTF с аннотациями не прошел проверку.")
    temporary.replace(target)
    return target


def strip_annotations(source: Path, destination: Path | None = None) -> Path:
    raw = source.read_text(encoding="latin-1")
    groups = _group_index(tokenize_rtf(raw))
    ranges = [(group.start, group.end) for group in groups if group.destination in {"atrfstart", "atrfend", "atnid", "atnauthor", "annotation"}]
    for start, end in sorted(ranges, reverse=True): raw = raw[:start] + raw[end:]
    target = destination or source
    temporary = target.with_name(target.name + ".tmp.rtf")
    temporary.write_text(raw, encoding="latin-1")
    if inspect_rtf(temporary):
        temporary.unlink(missing_ok=True); raise ValueError("RTF после удаления аннотаций поврежден.")
    temporary.replace(target)
    return target


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def rtf_fingerprints(path: Path) -> dict[str, str]:
    data = extract_rtf(path)
    text = "\n".join(block["текст"] for block in data["блоки"])
    structure = json.dumps([
        (
            block["номер"],
            block["тип"],
            [(bool(fragment["полужирный"]), bool(fragment["курсив"])) for fragment in block["фрагменты"]],
        )
        for block in data["блоки"]
    ], ensure_ascii=False)
    return {
        "текст": hashlib.sha256(text.encode()).hexdigest(),
        "структура": hashlib.sha256(structure.encode()).hexdigest(),
        "файл": file_sha256(path),
    }


def annotations_only_change(before: dict[str, str], after: dict[str, str]) -> bool:
    return before.get("текст") == after.get("текст") and before.get("структура") == after.get("структура")


def natural_key(path: Path) -> tuple:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name))


def discover_chapters(project_dir: Path) -> list[Path]:
    source = project_dir.resolve() / "input"
    if not source.is_dir(): raise ValueError("Каталог input/ не найден; сначала выполните $book-translator-init.")
    chapters = [path for path in source.iterdir() if path.is_file() and path.suffix.casefold() == ".rtf"]
    unsupported = [path.name for path in source.iterdir() if path.is_file() and path.suffix.casefold() != ".rtf"]
    if unsupported: raise ValueError("В input/ найдены неподдерживаемые файлы: " + ", ".join(sorted(unsupported)))
    if not chapters: raise ValueError("В input/ не найдены главы .rtf.")
    folded = [path.name.casefold() for path in chapters]
    if len(folded) != len(set(folded)): raise ValueError("Имена входных RTF неоднозначны с учетом регистра.")
    return sorted(chapters, key=natural_key)


def chapter_id(path: Path) -> str:
    return hashlib.sha256(path.name.casefold().encode("utf-8")).hexdigest()[:20]


def build_manifest(project_dir: Path, chapters: list[Path] | None = None) -> dict:
    project_dir = project_dir.resolve(); chapters = chapters or discover_chapters(project_dir)
    manifest = {"версия": 2, "главы": [{"id": chapter_id(path), "имя": path.name, "путь": path.relative_to(project_dir).as_posix(), "sha256": file_sha256(path)} for path in chapters]}
    target = project_dir / "work" / "manifest.json"; target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp"); temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); temporary.replace(target)
    return manifest


def refresh_manifest(project_dir: Path, manifest: dict, allow_changed: str | None = None) -> tuple[dict, list[dict], list[str]]:
    current = discover_chapters(project_dir); old = {item["имя"].casefold(): item for item in manifest.get("главы", [])}
    queue: list[dict] = []; conflicts: list[str] = []; entries: list[dict] = []
    for path in current:
        previous = old.get(path.name.casefold()); digest = file_sha256(path)
        entry = {"id": chapter_id(path), "имя": path.name, "путь": path.relative_to(project_dir).as_posix(), "sha256": digest}
        entries.append(entry)
        if previous is None: queue.append(entry)
        elif previous.get("sha256") != digest:
            if allow_changed and path.name.casefold() == allow_changed.casefold(): queue.append(entry)
            else: conflicts.append(f"Исходник «{path.name}» изменен; выберите перевести-заново или восстановите прежнюю версию.")
    missing = [item["имя"] for key, item in old.items() if key not in {path.name.casefold() for path in current}]
    conflicts.extend(f"Исходник «{name}» удален." for name in missing)
    return {"версия": 2, "главы": entries}, queue, conflicts


def verify_manifest(project_dir: Path, manifest: dict) -> list[str]:
    _, _, conflicts = refresh_manifest(project_dir, manifest)
    return conflicts


def validate_translation(source_blocks: list[dict], target_blocks: list[dict]) -> list[str]:
    errors: list[str] = []
    if len(source_blocks) != len(target_blocks): errors.append("Количество блоков перевода не совпадает с оригиналом.")
    for index, block in enumerate(target_blocks, start=1):
        text = str(block.get("текст", ""))
        fragments = block.get("фрагменты")
        if isinstance(fragments, list): text = "".join(str(item.get("текст", "")) for item in fragments)
        if "ё" in text or "Ё" in text: errors.append(f"Блок {index} содержит запрещенную букву ё.")
        if index <= len(source_blocks):
            source_fragments = source_blocks[index - 1].get("фрагменты")
            target_fragments = block.get("фрагменты")
            if isinstance(source_fragments, list) and isinstance(target_fragments, list):
                source_formats = [(bool(item.get("полужирный")), bool(item.get("курсив"))) for item in source_fragments]
                target_formats = [(bool(item.get("полужирный")), bool(item.get("курсив"))) for item in target_fragments]
                if source_formats != target_formats: errors.append(f"Блок {index} изменяет форматирующие фрагменты.")
    return errors


def check_output_conflicts(project_dir: Path, chapters: list[Path]) -> list[str]:
    names = [f"{path.stem}.rtf".casefold() for path in chapters]
    return ["Несколько исходников создают одинаковое имя результата."] if len(names) != len(set(names)) else []
