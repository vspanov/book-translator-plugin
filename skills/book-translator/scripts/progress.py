from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 2
PROJECT_MARKER_NAME = ".book-translator-project.json"
STATE_FILES = (
    "glossary.md", "decisions.md", "characters.md", "style-guide.md",
    "story-state.md", "chapter-summaries.md",
)
VALID_MODES = {"продолжить", "начать-заново", "перевести-заново"}
VALID_REVIEW_MODES = {"после-каждого-файла", "в-финале"}


def _json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _copy_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(target)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted((entry for entry in path.rglob("*") if entry.is_file()), key=lambda entry: entry.relative_to(path).as_posix()):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(file_sha256(item).encode())
    return digest.hexdigest()


def _asset_root() -> Path:
    return Path(__file__).resolve().parents[1] / "assets"


def _marker(project_dir: Path) -> Path:
    return project_dir / PROJECT_MARKER_NAME


def has_project_identity(value: object) -> bool:
    return isinstance(value, dict) and value.get("тип") == "book-translator" and value.get("схема") == SCHEMA_VERSION


def is_unsafe_link(path: Path) -> bool:
    return path.is_symlink()


def ensure_project(project_dir: Path) -> dict:
    project_dir = project_dir.resolve()
    try:
        value = json.loads(_marker(project_dir).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Каталог не инициализирован для book-translator 0.2; выполните $book-translator-init.") from error
    if not has_project_identity(value):
        raise ValueError("Старая или неизвестная схема проекта не поддерживается; создайте новый каталог.")
    return value


def install_agents(project_dir: Path, overwrite: set[str] | None = None) -> list[str]:
    overwrite = overwrite or set()
    source_dir = _asset_root() / "agents"
    target_dir = project_dir / ".codex" / "agents"
    target_dir.mkdir(parents=True, exist_ok=True)
    conflicts: list[str] = []
    for source in sorted(source_dir.glob("*.toml")):
        target = target_dir / source.name
        if not target.exists():
            _copy_atomic(source, target)
        elif file_sha256(source) != file_sha256(target):
            if source.name in overwrite:
                _copy_atomic(source, target)
            else:
                conflicts.append(source.name)
    return conflicts


def initialize_project(project_dir: Path, overwrite_agents: set[str] | None = None) -> dict:
    project_dir = project_dir.resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    marker_path = _marker(project_dir)
    if marker_path.exists():
        marker = ensure_project(project_dir)
    else:
        legacy = (project_dir / "progress.json").exists() or (project_dir / "work" / "manifest.json").exists()
        if legacy:
            raise ValueError("Обнаружен проект прежней схемы. Автоматическая миграция намеренно не выполняется.")
        marker = {"тип": "book-translator", "схема": SCHEMA_VERSION, "создан": datetime.now(timezone.utc).isoformat()}
        _json_atomic(marker_path, marker)
    for name in ("input", "output", "state", "work"):
        target = project_dir / name
        if target.exists() and (not target.is_dir() or target.is_symlink()):
            raise ValueError(f"Путь {name}/ небезопасен.")
        target.mkdir(exist_ok=True)
    assets = _asset_root()
    config_target = project_dir / "config.ini"
    if not config_target.exists(): _copy_atomic(assets / "config.ini", config_target)
    readme_target = project_dir / "TRANSLATION.md"
    if not readme_target.exists(): _copy_atomic(assets / "project-readme.md", readme_target)
    for name in STATE_FILES:
        target = project_dir / "state" / name
        if not target.exists(): _copy_atomic(assets / name, target)
    conflicts = install_agents(project_dir, overwrite_agents)
    progress = project_dir / "progress.json"
    if not progress.exists():
        _json_atomic(progress, new_progress())
    return {"проект": str(project_dir), "конфликты_агентов": conflicts, "маркер": marker}


def new_progress() -> dict:
    return {
        "схема": SCHEMA_VERSION,
        "статус_книги": "ожидает-запуска",
        "этап": "ожидание",
        "текущий_файл": None,
        "очередь": [],
        "файлы": {},
        "ожидают_одобрения": [],
        "ошибка": None,
    }


def load_progress(project_dir: Path) -> dict:
    ensure_project(project_dir)
    try:
        value = json.loads((project_dir / "progress.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("progress.json поврежден.") from error
    if not isinstance(value, dict) or value.get("схема") != SCHEMA_VERSION:
        raise ValueError("progress.json относится к неподдерживаемой схеме.")
    return value


def save_progress(project_dir: Path, progress: dict) -> None:
    if progress.get("схема") != SCHEMA_VERSION: raise ValueError("Нельзя записать progress.json другой схемы.")
    _json_atomic(project_dir / "progress.json", progress)


def load_config(project_dir: Path, overrides: dict | None = None) -> dict:
    ensure_project(project_dir)
    parser = configparser.ConfigParser()
    try:
        with (project_dir / "config.ini").open(encoding="utf-8") as stream: parser.read_file(stream)
        section = parser["перевод"]
    except (OSError, KeyError, configparser.Error) as error:
        raise ValueError("config.ini должен содержать раздел [перевод].") from error
    config = {
        "режим": section.get("режим", "продолжить").strip(),
        "максимум_циклов": section.getint("максимум_циклов", fallback=5),
        "пользовательская_верификация": section.get("пользовательская_верификация", "в_финале").strip(),
    }
    config.update({key: value for key, value in (overrides or {}).items() if value is not None})
    if config["режим"] not in VALID_MODES: raise ValueError("Неизвестный режим перевода.")
    if type(config["максимум_циклов"]) is not int or not 1 <= config["максимум_циклов"] <= 20:
        raise ValueError("максимум_циклов должен быть целым числом от 1 до 20.")
    if config["пользовательская_верификация"] not in VALID_REVIEW_MODES:
        raise ValueError("пользовательская_верификация: после-каждого-файла или в-финале.")
    return config


def parse_request_arguments(arguments: list[str]) -> dict:
    result: dict = {}
    for argument in arguments:
        if argument in VALID_MODES: result["режим"] = argument
        elif argument.startswith("циклы="):
            value = argument.split("=", 1)[1]
            if not re.fullmatch(r"\d+", value): raise ValueError("циклы должны быть целым числом от 1 до 20.")
            result["максимум_циклов"] = int(value)
        elif argument.startswith("верификация="):
            result["пользовательская_верификация"] = argument.split("=", 1)[1]
    if "максимум_циклов" in result and not 1 <= result["максимум_циклов"] <= 20:
        raise ValueError("циклы должны быть целым числом от 1 до 20.")
    if result.get("пользовательская_верификация", "в-финале") not in VALID_REVIEW_MODES:
        raise ValueError("Неизвестный режим пользовательской верификации.")
    return result


def verification_schedule(maximum: int) -> list[dict]:
    if type(maximum) is not int or not 1 <= maximum <= 20: raise ValueError("Лимит проверок должен быть от 1 до 20.")
    return [{"раунд": number, "verifier": True, "editor_после": number < maximum} for number in range(1, maximum + 1)]


def execute_verification_cycles(draft, maximum: int, verifier, editor) -> tuple[object, list[dict]]:
    reports: list[dict] = []
    current = draft
    for step in verification_schedule(maximum):
        report = verifier(current, step["раунд"])
        if not isinstance(report, dict) or not isinstance(report.get("замечания"), list):
            raise ValueError("Verifier вернул некорректный отчет.")
        reports.append(report)
        if not report["замечания"]: break
        if step["editor_после"]: current = editor(current, report, step["раунд"])
    return current, reports


def record_round(project_dir: Path, chapter_identifier: str, report: dict, edited: bool) -> dict:
    progress = load_progress(project_dir)
    item = progress["файлы"].setdefault(chapter_identifier, {"раунды": [], "ревизия": 0})
    rounds = item.setdefault("раунды", [])
    rounds.append({"номер": len(rounds) + 1, "отчет": report, "после_правок": bool(edited)})
    progress["этап"] = "автоматическая-верификация"
    save_progress(project_dir, progress)
    return rounds[-1]


def _managed_pattern(chapter_identifier: str) -> re.Pattern[str]:
    escaped = re.escape(chapter_identifier)
    return re.compile(rf"\n?<!-- managed:file:{escaped}:start -->.*?<!-- managed:file:{escaped}:end -->\n?", re.DOTALL)


def prepare_state(project_dir: Path, chapter_identifier: str, destination: Path) -> Path:
    ensure_project(project_dir)
    destination.mkdir(parents=True, exist_ok=False)
    for name in STATE_FILES:
        source = project_dir / "state" / name
        text = source.read_text(encoding="utf-8")
        cleaned = _managed_pattern(chapter_identifier).sub("\n", text)
        (destination / name).write_text(cleaned, encoding="utf-8")
    return destination


def replace_managed_contribution(state_dir: Path, chapter_identifier: str, contributions: dict[str, str]) -> None:
    unknown = set(contributions) - set(STATE_FILES)
    if unknown: raise ValueError("Неизвестные state-файлы: " + ", ".join(sorted(unknown)))
    for name in STATE_FILES:
        path = state_dir / name
        text = path.read_text(encoding="utf-8")
        text = _managed_pattern(chapter_identifier).sub("\n", text).rstrip() + "\n"
        contribution = contributions.get(name, "").strip()
        if contribution:
            text += f"\n<!-- managed:file:{chapter_identifier}:start -->\n{contribution}\n<!-- managed:file:{chapter_identifier}:end -->\n"
        path.write_text(text, encoding="utf-8")


def resolve_chapter(name: str, entries: list[dict]) -> dict:
    folded = name.casefold().removesuffix(".rtf")
    exact = [item for item in entries if item["имя"].casefold().removesuffix(".rtf") == folded]
    if len(exact) == 1: return exact[0]
    matches = [item for item in entries if folded in item["имя"].casefold()]
    if len(matches) == 1: return matches[0]
    if not matches: raise ValueError(f"Файл «{name}» не найден.")
    raise ValueError("Имя неоднозначно. Совпадения: " + ", ".join(item["имя"] for item in matches))


def output_name(source_name: str, revision: int, timestamp: datetime | None = None) -> str:
    stem = Path(source_name).stem
    if revision <= 1: return f"{stem}.ru.rtf"
    moment = timestamp or datetime.now().astimezone()
    return f"{stem}.ru.v{revision:03d}.{moment.strftime('%Y%m%d')}.rtf"


def _revision_dir(project_dir: Path, chapter_identifier: str, revision: int) -> Path:
    if not re.fullmatch(r"[a-f0-9]{8,64}", chapter_identifier): raise ValueError("Некорректный идентификатор файла.")
    return project_dir / "work" / "revisions" / chapter_identifier / f"{revision:03d}"


def prepare_transaction(
    project_dir: Path,
    chapter: dict,
    candidate: Path,
    prepared_state: Path,
    reports: list[dict] | None = None,
) -> Path:
    ensure_project(project_dir)
    from documents import inspect_rtf
    mechanical_errors = inspect_rtf(candidate)
    if mechanical_errors:
        raise ValueError("Кандидат не прошел механическую проверку: " + " ".join(mechanical_errors))
    progress = load_progress(project_dir)
    identifier = chapter["id"]
    previous = progress["файлы"].get(identifier, {})
    revision = int(previous.get("ревизия", 0)) + 1
    revision_dir = _revision_dir(project_dir, identifier, revision)
    if revision_dir.exists(): raise ValueError("Каталог этой ревизии уже существует.")
    revision_dir.mkdir(parents=True)
    backup = revision_dir / "backup"; backup.mkdir()
    progress_hash = file_sha256(project_dir / "progress.json")
    state_hash = directory_sha256(project_dir / "state")
    shutil.copy2(project_dir / "progress.json", backup / "progress.json")
    shutil.copytree(project_dir / "state", backup / "state")
    if file_sha256(backup / "progress.json") != progress_hash or directory_sha256(backup / "state") != state_hash:
        raise ValueError("Не удалось проверить резервную копию progress или state.")
    previous_output = previous.get("последний_результат")
    previous_output_hash = None
    if isinstance(previous_output, str) and (project_dir / "output" / previous_output).is_file():
        previous_output_hash = file_sha256(project_dir / "output" / previous_output)
        shutil.copy2(project_dir / "output" / previous_output, backup / previous_output)
        if file_sha256(backup / previous_output) != previous_output_hash:
            raise ValueError("Не удалось проверить резервную копию прежнего перевода.")
    previous_revision_hash = None
    if revision > 1:
        previous_revision = _revision_dir(project_dir, identifier, revision - 1)
        if not (previous_revision / "завершено").is_file():
            raise ValueError("Артефакты предыдущей ревизии не подтверждены.")
        previous_revision_hash = directory_sha256(previous_revision)
    staged = revision_dir / "candidate.rtf"; shutil.copy2(candidate, staged)
    shutil.copytree(prepared_state, revision_dir / "prepared-state")
    if reports is not None: _json_atomic(revision_dir / "verification-reports.json", {"раунды": reports})
    metadata = {
        "схема": SCHEMA_VERSION, "id_файла": identifier, "исходник": chapter["имя"],
        "sha256_исходника": chapter["sha256"], "ревизия": revision,
        "имя_результата": output_name(chapter["имя"], revision),
        "sha256_кандидата": file_sha256(staged), "sha256_state": directory_sha256(revision_dir / "prepared-state"),
        "резервная_копия": {"sha256_progress": progress_hash, "sha256_state": state_hash, "предыдущий_результат": previous_output, "sha256_предыдущего_результата": previous_output_hash, "sha256_предыдущей_ревизии": previous_revision_hash},
    }
    _json_atomic(revision_dir / "transaction.json", metadata)
    (revision_dir / "готово-к-фиксации").write_text("да\n", encoding="utf-8")
    return revision_dir


def _restore_backup(project_dir: Path, revision_dir: Path) -> None:
    backup = revision_dir / "backup"
    replacement = revision_dir / "restored-state"
    if replacement.exists(): shutil.rmtree(replacement)
    shutil.copytree(backup / "state", replacement)
    old_state = project_dir / "state"
    failed_state = revision_dir / "failed-state"
    if old_state.exists(): old_state.replace(failed_state)
    replacement.replace(old_state)
    _copy_atomic(backup / "progress.json", project_dir / "progress.json")


def commit_transaction(project_dir: Path, revision_dir: Path) -> Path:
    from documents import extract_annotations, inspect_rtf, rtf_fingerprints
    metadata = json.loads((revision_dir / "transaction.json").read_text(encoding="utf-8"))
    candidate = revision_dir / "candidate.rtf"; staged_state = revision_dir / "prepared-state"
    if file_sha256(candidate) != metadata["sha256_кандидата"] or directory_sha256(staged_state) != metadata["sha256_state"]:
        raise ValueError("Кандидат или подготовленный state изменились после проверки.")
    errors = inspect_rtf(candidate)
    if errors: raise ValueError("Собранный RTF поврежден: " + " ".join(errors))
    output = project_dir / "output" / metadata["имя_результата"]
    if output.exists(): raise ValueError("Имя новой ревизии уже занято.")
    state_swap = revision_dir / "state-to-publish"; shutil.copytree(staged_state, state_swap)
    old_state = project_dir / "state"; archived_state = revision_dir / "published-state-backup"
    try:
        old_state.replace(archived_state)
        state_swap.replace(old_state)
        _copy_atomic(candidate, output)
        progress = load_progress(project_dir)
        item = progress["файлы"].setdefault(metadata["id_файла"], {})
        old_result = item.get("последний_результат")
        history = item.setdefault("история_результатов", [])
        if old_result and old_result not in history: history.append(old_result)
        item.update({
            "имя": metadata["исходник"], "sha256_исходника": metadata["sha256_исходника"],
            "ревизия": metadata["ревизия"], "последний_результат": metadata["имя_результата"],
            "sha256_результата": file_sha256(output), "статус": "ожидает-одобрения",
            "отпечатки_при_публикации": rtf_fingerprints(output),
            "аннотации_при_публикации": [note["id"] for note in extract_annotations(output)],
        })
        if metadata["id_файла"] not in progress["ожидают_одобрения"]: progress["ожидают_одобрения"].append(metadata["id_файла"])
        progress["очередь"] = [value for value in progress.get("очередь", []) if value != metadata["id_файла"]]
        review_mode = progress.get("настройки_запуска", {}).get("пользовательская_верификация", "в-финале")
        keep_working = review_mode == "в-финале" and bool(progress["очередь"])
        progress.update({
            "статус_книги": "в-работе" if keep_working else "ожидает-одобрения",
            "этап": "подготовка-state" if keep_working else "пользовательская-верификация",
            "текущий_файл": None if keep_working else metadata["исходник"], "ошибка": None,
        })
        save_progress(project_dir, progress)
        (revision_dir / "завершено").write_text("да\n", encoding="utf-8")
        return output
    except Exception as error:
        output.unlink(missing_ok=True)
        if old_state.exists(): shutil.rmtree(old_state)
        if archived_state.exists(): archived_state.replace(old_state)
        _copy_atomic(revision_dir / "backup" / "progress.json", project_dir / "progress.json")
        (revision_dir / "откачено").write_text(str(error), encoding="utf-8")
        raise ValueError(f"Публикация не удалась; предыдущий перевод и state восстановлены: {error}") from error


def approve_files(project_dir: Path, identifiers: list[str], strip_callback=None) -> dict:
    from documents import annotations_only_change, rtf_fingerprints
    if strip_callback is None:
        from documents import strip_annotations
        strip_callback = strip_annotations
    progress = load_progress(project_dir)
    for identifier in identifiers:
        item = progress["файлы"].get(identifier)
        if not item: raise ValueError(f"Неизвестный файл: {identifier}")
        result = project_dir / "output" / item["последний_результат"]
        current = rtf_fingerprints(result)
        if not annotations_only_change(item.get("отпечатки_при_публикации", {}), current):
            raise ValueError(f"Основной текст или структура «{result.name}» изменены напрямую.")
        strip_callback(result)
        item["sha256_результата"] = file_sha256(result)
        item["отпечатки_при_публикации"] = rtf_fingerprints(result)
        item["аннотации_при_публикации"] = []
        item["статус"] = "одобрен"
        progress["ожидают_одобрения"] = [value for value in progress["ожидают_одобрения"] if value != identifier]
    if not progress["ожидают_одобрения"] and not progress["очередь"]:
        progress.update({"статус_книги": "готово", "этап": "готово", "текущий_файл": None})
        (project_dir / "work" / "active.json").unlink(missing_ok=True)
    elif not progress["ожидают_одобрения"] and progress["очередь"]:
        progress.update({"статус_книги": "в-работе", "этап": "подготовка-state", "текущий_файл": None})
    save_progress(project_dir, progress)
    return progress


def register_feedback(project_dir: Path, identifier: str, annotation_ids: list[str]) -> list[str]:
    progress = load_progress(project_dir)
    item = progress["файлы"].get(identifier)
    if not item: raise ValueError("Файл для обратной связи не найден.")
    handled = set(item.setdefault("обработанные_аннотации", []))
    fresh = [value for value in annotation_ids if value not in handled]
    item["обработанные_аннотации"].extend(fresh)
    item["статус"] = "на-доработке"
    progress.update({"статус_книги": "в-работе", "этап": "учет-замечаний", "текущий_файл": item["имя"]})
    save_progress(project_dir, progress)
    return fresh


def scan_published_feedback(project_dir: Path, only_identifier: str | None = None) -> tuple[dict[str, list[dict]], list[str]]:
    from documents import annotations_only_change, extract_annotations, rtf_fingerprints
    progress = load_progress(project_dir)
    feedback: dict[str, list[dict]] = {}
    errors: list[str] = []
    for identifier, item in progress["файлы"].items():
        if only_identifier is not None and identifier != only_identifier: continue
        name = item.get("последний_результат")
        if not isinstance(name, str): continue
        path = project_dir / "output" / name
        if not path.is_file(): errors.append(f"Опубликованный файл «{name}» отсутствует."); continue
        baseline = item.get("отпечатки_при_публикации", {})
        current = rtf_fingerprints(path)
        if not annotations_only_change(baseline, current):
            errors.append(f"Основной текст или структура «{name}» изменены напрямую.")
            continue
        ignored = set(item.get("аннотации_при_публикации", [])) | set(item.get("обработанные_аннотации", []))
        fresh = [note for note in extract_annotations(path) if note["id"] not in ignored]
        if fresh: feedback[identifier] = fresh
    return feedback, errors


def scan_queue(project_dir: Path, allow_changed: str | None = None) -> tuple[dict, list[dict], list[str]]:
    from documents import build_manifest, discover_chapters, file_sha256 as source_sha256, refresh_manifest
    ensure_project(project_dir)
    manifest_path = project_dir / "work" / "manifest.json"
    progress = load_progress(project_dir)
    if not manifest_path.exists():
        manifest = build_manifest(project_dir, discover_chapters(project_dir))
        queue = [item for item in manifest["главы"] if item["id"] not in progress["файлы"]]
        return manifest, queue, []
    try: manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error: raise ValueError("Манифест поврежден.") from error
    refreshed, new_entries, conflicts = refresh_manifest(project_dir, manifest, allow_changed)
    by_id = {item["id"]: item for item in refreshed["главы"]}
    queue = list(new_entries)
    for identifier, entry in by_id.items():
        saved = progress["файлы"].get(identifier)
        if saved is None and entry not in queue: queue.append(entry)
        elif saved is not None and saved.get("sha256_исходника") != entry["sha256"]:
            if allow_changed and saved.get("имя", "").casefold() == allow_changed.casefold():
                if entry not in queue: queue.append(entry)
            elif not any(saved.get("имя", "") in message for message in conflicts):
                conflicts.append(f"Исходник «{saved.get('имя')}» изменен; требуется перевести-заново.")
    if not conflicts:
        _json_atomic(manifest_path, refreshed)
    return refreshed, queue, conflicts


def activate(project_dir: Path, config: dict, queue: list[dict]) -> dict:
    progress = load_progress(project_dir)
    progress.update({"статус_книги": "в-работе", "этап": "подготовка-state", "очередь": [item["id"] for item in queue], "настройки_запуска": config, "ошибка": None})
    save_progress(project_dir, progress)
    _json_atomic(project_dir / "work" / "active.json", {"тип": "book-translator", "схема": SCHEMA_VERSION, "проект": str(project_dir.resolve())})
    return progress


def check_completed_chapter(project_dir: Path, chapter_name: str) -> list[str]:
    try: progress = load_progress(project_dir)
    except ValueError as error: return [str(error)]
    matches = [item for item in progress["файлы"].values() if item.get("имя") == chapter_name]
    if len(matches) != 1: return [f"Файл «{chapter_name}» не зарегистрирован как завершенный."]
    item = matches[0]; result = project_dir / "output" / str(item.get("последний_результат", ""))
    if not result.is_file(): return [f"Результат файла «{chapter_name}» отсутствует."]
    if file_sha256(result) != item.get("sha256_результата"): return [f"Результат файла «{chapter_name}» изменен напрямую."]
    return []


def check_consistency(project_dir: Path) -> list[str]:
    try: progress = load_progress(project_dir)
    except ValueError as error: return [str(error)]
    errors: list[str] = []
    for item in progress.get("файлы", {}).values():
        if item.get("статус") in {"одобрен", "ожидает-одобрения"}: errors.extend(check_completed_chapter(project_dir, item.get("имя", "")))
    return errors


def finish_book(project_dir: Path) -> None:
    progress = load_progress(project_dir)
    if progress["очередь"] or progress["ожидают_одобрения"]: raise ValueError("Проект нельзя завершить до обработки очереди и явного одобрения.")
    if check_consistency(project_dir): raise ValueError("Проект несогласован.")
    progress.update({"статус_книги": "готово", "этап": "готово", "текущий_файл": None})
    save_progress(project_dir, progress)
    (project_dir / "work" / "active.json").unlink(missing_ok=True)


def restart_project(project_dir: Path, confirmed: bool = False) -> list[Path] | Path:
    ensure_project(project_dir)
    affected = [project_dir / "output", project_dir / "state", project_dir / "progress.json", project_dir / "work"]
    if not confirmed: return affected
    for root in affected:
        candidates = [root, *(root.rglob("*") if root.is_dir() else [])]
        if any(path.is_symlink() for path in candidates): raise ValueError(f"Нельзя безопасно очистить {root.name}: найден символический путь.")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = project_dir / "work" / "restarts" / timestamp
    backup.mkdir(parents=True)
    shutil.copytree(project_dir / "output", backup / "output")
    shutil.copytree(project_dir / "state", backup / "state")
    shutil.copy2(project_dir / "progress.json", backup / "progress.json")
    work_backup = backup / "work"; work_backup.mkdir()
    for item in (project_dir / "work").iterdir():
        if item.name == "restarts": continue
        if item.is_dir(): shutil.copytree(item, work_backup / item.name)
        elif item.is_file(): shutil.copy2(item, work_backup / item.name)
    for item in (project_dir / "output").iterdir():
        if item.is_dir(): shutil.rmtree(item)
        else: item.unlink()
    for item in (project_dir / "state").iterdir():
        if item.is_dir(): shutil.rmtree(item)
        else: item.unlink()
    for item in list((project_dir / "work").iterdir()):
        if item.name == "restarts": continue
        if item.is_dir(): shutil.rmtree(item)
        else: item.unlink()
    (project_dir / "progress.json").unlink()
    initialize_project(project_dir)
    return backup
