from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
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
PROGRESS_KEYS = (
    "версия",
    "статус_книги",
    "текущая_глава",
    "этап",
    "последняя_готовая_глава",
    "ошибка",
)
BOOK_STATUSES = {"не_начат", "в_работе", "ошибка", "готово"}
KNOWN_STAGES = {None, "ожидает_извлечения", *STAGE_AFTER, *STAGE_AFTER.values()}


def is_unsafe_link(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        attributes = 0
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


_is_link = is_unsafe_link


def _project_directory(project_dir: Path, name: str) -> Path:
    path = project_dir / name
    if _is_link(path) or not path.is_dir() or path.resolve().parent != project_dir:
        raise ValueError(f"Путь {name}/ внутри проекта небезопасен.")
    return path


def _transactions_directory(project_dir: Path, create: bool = False) -> Path:
    work = _project_directory(project_dir, "work")
    transactions = work / "transactions"
    if (
        _is_link(transactions)
        or (transactions.exists() and not transactions.is_dir())
        or transactions.resolve().parent != work.resolve()
    ):
        raise ValueError("Путь work/transactions/ внутри проекта небезопасен.")
    if create:
        transactions.mkdir(exist_ok=True)
    return transactions


def _transaction_directory(transactions: Path, path: Path) -> Path:
    transaction = Path(os.path.abspath(path))
    if (
        transaction.parent != transactions.absolute()
        or _is_link(transaction)
        or not transaction.is_dir()
        or transaction.resolve().parent != transactions.resolve()
    ):
        raise ValueError("каталог транзакции небезопасен.")
    return transaction


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
        write_json_atomic(
            progress_path,
            {
                "версия": 1,
                "статус_книги": "не_начат",
                "текущая_глава": None,
                "этап": None,
                "последняя_готовая_глава": None,
                "ошибка": None,
            },
        )


def write_json_atomic(path: Path, value: dict) -> None:
    _write_bytes_atomic(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def load_progress(project_dir: Path) -> dict:
    progress_path = project_dir.resolve() / "progress.json"
    if _is_link(progress_path):
        raise ValueError("Путь progress.json внутри проекта небезопасен.")
    try:
        value = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Не удалось прочитать progress.json.") from error
    if not isinstance(value, dict):
        raise ValueError("progress.json должен содержать объект.")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_text_atomic(path: Path, text: str) -> None:
    _write_bytes_atomic(path, text.encode("utf-8"))


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = None
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _chapter_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or Path(value).name != value
    ):
        raise ValueError("Имя главы должно быть простым именем файла.")
    return value


def start_chapter(project_dir: Path, chapter_name: str) -> None:
    project_dir = project_dir.resolve()
    chapter_name = _chapter_name(chapter_name)
    active_path = _project_directory(project_dir, "work") / "active.json"
    state = load_progress(project_dir)
    active_chapter = None
    if _is_link(active_path) or (active_path.exists() and not active_path.is_file()):
        raise ValueError("Маркер активной главы небезопасен.")
    if active_path.exists():
        active = _load_json(active_path, "маркер активной главы")
        if active.get("проект") != str(project_dir):
            raise ValueError("Маркер активной главы относится к другому проекту.")
        try:
            active_chapter = _chapter_name(active.get("глава"))
        except ValueError as error:
            raise ValueError("Маркер активной главы содержит некорректную главу.") from error
    if state.get("статус_книги") == "ошибка" or state.get("ошибка"):
        raise ValueError("Нельзя начать следующую главу при зафиксированной ошибке.")
    partial_start = active_chapter is not None and state.get("этап") == "готово" and (
        active_chapter != state.get("текущая_глава")
        or active_chapter != state.get("последняя_готовая_глава")
    )
    if partial_start and chapter_name != active_chapter:
        raise ValueError(
            f"Глава «{active_chapter}» уже частично начата; сначала повторите её запуск."
        )
    if (
        state.get("текущая_глава") is not None and state.get("этап") != "готово"
    ) or (active_path.exists() and state.get("этап") != "готово"):
        raise ValueError("В проекте уже активна незавершённая глава.")
    state.update(
        {
            "статус_книги": "в_работе",
            "текущая_глава": chapter_name,
            "этап": "ожидает_извлечения",
            "ошибка": None,
            "редакторские_циклы": 1,
            "артефакты": {},
        }
    )
    if not partial_start:
        write_json_atomic(
            active_path,
            {
                "проект": str(project_dir),
                "глава": chapter_name,
                "время_начала": _utc_now(),
            },
        )
    write_json_atomic(project_dir / "progress.json", state)


def _expected_stage(state: dict) -> str | None:
    if state.get("этап") == "проверка_2" and state.get("редакторские_циклы") == 2:
        return "редактура_2"
    return STAGE_AFTER.get(state.get("этап"))


def advance_stage(project_dir: Path, completed_stage: str, artifact: str) -> None:
    project_dir = project_dir.resolve()
    state = load_progress(project_dir)
    if state.get("статус_книги") == "ошибка":
        raise ValueError("Нельзя продолжить главу с зафиксированной ошибкой.")
    expected = _expected_stage(state)
    if completed_stage != expected:
        raise ValueError(
            f"Нельзя завершить этап {completed_stage}: ожидался {expected or 'допустимый этап'}."
        )
    if not isinstance(artifact, str) or not artifact:
        raise ValueError("Для завершённого этапа нужен путь к артефакту.")
    state["этап"] = completed_stage
    state.setdefault("артефакты", {})[completed_stage] = artifact
    write_json_atomic(project_dir / "progress.json", state)


def record_failure(project_dir: Path, stage: str, explanation: str) -> None:
    project_dir = project_dir.resolve()
    state = load_progress(project_dir)
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("Нужно объяснить причину ошибки.")
    if stage not in {state.get("этап"), _expected_stage(state)}:
        raise ValueError("Ошибка относится не к текущему или ожидаемому этапу.")
    state.update({"статус_книги": "ошибка", "этап": stage, "ошибка": explanation})
    write_json_atomic(project_dir / "progress.json", state)
    active_path = _project_directory(project_dir, "work") / "active.json"
    if active_path.is_file():
        try:
            active = json.loads(active_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            active = {"проект": str(project_dir), "глава": state.get("текущая_глава")}
        active.update({"этап_ошибки": stage, "объяснение": explanation})
        write_json_atomic(active_path, active)


def request_second_edit(project_dir: Path) -> None:
    project_dir = project_dir.resolve()
    state = load_progress(project_dir)
    stage = state.get("этап")
    if stage == "проверка_3":
        raise ValueError("третий редакторский цикл запрещён.")
    if stage != "проверка_2":
        raise ValueError("Второй редакторский цикл разрешён только после проверки_2.")
    if state.get("редакторские_циклы", 1) >= 2:
        raise ValueError("второй цикл уже запрошен.")
    state["редакторские_циклы"] = 2
    write_json_atomic(project_dir / "progress.json", state)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_sha256(path: Path) -> str:
    if _is_link(path):
        raise ValueError(f"Ссылка на каталог запрещена: {path}.")
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"Каталог не найден: {path}.")
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*"), key=lambda value: value.relative_to(path).as_posix()):
        if _is_link(item):
            raise ValueError(f"Ссылка внутри каталога запрещена: {item}.")
        if item.is_file():
            digest.update(item.relative_to(path).as_posix().encode("utf-8"))
            digest.update(b"\0")
            with item.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _remove_within(path: Path, parent: Path) -> None:
    absolute = Path(path).absolute()
    parent_absolute = Path(parent).absolute()
    try:
        relative = absolute.relative_to(parent_absolute)
    except ValueError as error:
        raise ValueError("Отказано в удалении пути вне разрешённого каталога.") from error
    if not relative.parts:
        raise ValueError("Отказано в удалении корня разрешённого каталога.")
    if path.is_symlink():
        path.unlink(missing_ok=True)
    elif _is_link(path):
        path.rmdir() if path.is_dir() else path.unlink(missing_ok=True)
    elif path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        if not _inside(path, parent):
            raise ValueError("Отказано в удалении каталога по внешней ссылке.")
        shutil.rmtree(path)


def _validate_state(path: Path) -> None:
    if not path.is_dir():
        raise ValueError("Следующая версия памяти не найдена.")
    missing = [name for name in STATE_ASSETS if not (path / name).is_file()]
    if missing:
        raise ValueError("Следующая версия памяти неполна: " + ", ".join(missing) + ".")
    directory_sha256(path)


def _load_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Не удалось прочитать {description}.") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description} должен содержать объект.")
    return value


def _progress_schema_errors(value: dict, require_hashes: bool = False) -> list[str]:
    missing = [key for key in PROGRESS_KEYS if key not in value]
    if missing:
        return ["progress.json неполон: отсутствуют поля " + ", ".join(missing) + "."]
    errors = []
    if type(value["версия"]) is not int or value["версия"] != 1:
        errors.append("Версия progress.json должна быть равна 1.")
    if not isinstance(value["статус_книги"], str) or value["статус_книги"] not in BOOK_STATUSES:
        errors.append("progress.json содержит неизвестный статус книги.")
    if not (
        value["этап"] is None or isinstance(value["этап"], str)
    ) or value["этап"] not in KNOWN_STAGES:
        errors.append("progress.json содержит неизвестный этап.")
    for key in ("текущая_глава", "последняя_готовая_глава"):
        chapter = value[key]
        if chapter is not None:
            try:
                _chapter_name(chapter)
            except ValueError:
                errors.append(f"Поле «{key}» содержит небезопасное имя главы.")
    if value["текущая_глава"] is None and value["этап"] is not None:
        errors.append("Этап указан без текущей главы.")
    if value["текущая_глава"] is not None and value["этап"] is None:
        errors.append("Текущая глава указана без этапа.")
    if value["статус_книги"] == "не_начат" and any(
        value[key] is not None
        for key in ("текущая_глава", "этап", "последняя_готовая_глава", "ошибка")
    ):
        errors.append("Неначатая книга содержит рабочее состояние.")
    if value["этап"] == "готово" and (
        value["текущая_глава"] is None
        or value["текущая_глава"] != value["последняя_готовая_глава"]
    ):
        errors.append("Готовая контрольная точка не согласует текущую и последнюю главы.")
    if value["статус_книги"] == "готово" and value["этап"] != "готово":
        errors.append("Готовая книга не находится на этапе «готово».")
    error = value["ошибка"]
    if value["статус_книги"] == "ошибка":
        if not isinstance(error, str) or not error.strip():
            errors.append("Ошибка обработки не содержит объяснения.")
    elif error is not None:
        errors.append("Объяснение ошибки задано без статуса «ошибка».")
    if require_hashes and value["последняя_готовая_глава"] is not None:
        for key in ("sha256_результата", "sha256_памяти"):
            digest = value.get(key)
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                errors.append(f"progress.json не содержит корректное поле «{key}».")
    return errors


def _validate_next_progress(value: dict, chapter_name: str) -> None:
    errors = _progress_schema_errors(value)
    if (
        value.get("статус_книги") != "в_работе"
        or value.get("этап") != "готово"
        or value.get("текущая_глава") != chapter_name
        or value.get("последняя_готовая_глава") != chapter_name
        or value.get("ошибка") is not None
    ):
        errors.append("Контрольная точка не завершает указанную главу.")
    if errors:
        raise ValueError(
            "Следующая контрольная точка неполна или некорректна: "
            + " ".join(errors)
        )


def prepare_transaction(
    project_dir: Path,
    chapter_name: str,
    built_document: Path,
    next_state: Path,
    next_progress: dict,
) -> Path:
    project_dir = project_dir.resolve()
    chapter_name = _chapter_name(chapter_name)
    built_document = built_document.resolve()
    next_state = next_state.resolve()
    work_dir = _project_directory(project_dir, "work")
    if not _inside(built_document, work_dir) or not built_document.is_file():
        raise ValueError("Собранный документ должен быть файлом внутри work/.")
    if built_document.stat().st_size == 0:
        raise ValueError("Собранный документ пуст.")
    if not _inside(next_state, work_dir):
        raise ValueError("Следующая версия памяти должна находиться внутри work/.")
    _validate_state(next_state)
    if not isinstance(next_progress, dict):
        raise ValueError("Следующая контрольная точка должна быть объектом.")
    _validate_next_progress(next_progress, chapter_name)

    transactions = _transactions_directory(project_dir, create=True)
    transaction = transactions / hashlib.sha256(chapter_name.encode("utf-8")).hexdigest()
    if _is_link(transaction) or transaction.exists():
        raise ValueError("Путь транзакции этой главы уже существует или небезопасен.")
    transaction.mkdir()
    transaction = _transaction_directory(transactions, transaction)
    output_source = transaction / "new-output" / chapter_name
    state_source = transaction / "new-state"
    (transaction / "new-output").mkdir()
    (transaction / "backup").mkdir()
    shutil.copy2(built_document, output_source)
    shutil.copytree(next_state, state_source)
    write_json_atomic(transaction / "next-progress.json", dict(next_progress))
    checkpoint_hash = _file_sha256(transaction / "next-progress.json")
    write_json_atomic(
        transaction / "transaction.json",
        {
            "глава": chapter_name,
            "sha256_результата": _file_sha256(output_source),
            "sha256_памяти": directory_sha256(state_source),
            "sha256_контрольной_точки": checkpoint_hash,
        },
    )
    _validate_prepared(transaction)
    _write_text_atomic(transaction / "готово-к-фиксации", "да\n")
    return transaction


def _transaction_for_project(project_dir: Path, transaction_dir: Path) -> Path:
    transactions = _transactions_directory(project_dir.resolve())
    if not transactions.is_dir():
        raise ValueError("Каталог work/transactions/ не найден.")
    return _transaction_directory(transactions, transaction_dir)


def _validate_prepared(transaction: Path) -> dict:
    metadata = _load_json(transaction / "transaction.json", "описание транзакции")
    chapter_name = _chapter_name(metadata.get("глава"))
    output = transaction / "new-output" / chapter_name
    state = transaction / "new-state"
    _validate_state(state)
    next_progress = _load_json(
        transaction / "next-progress.json", "следующую контрольную точку"
    )
    if _file_sha256(transaction / "next-progress.json") != metadata.get(
        "sha256_контрольной_точки"
    ):
        raise ValueError("Подготовленная контрольная точка изменена.")
    _validate_next_progress(next_progress, chapter_name)
    if not output.is_file() or _file_sha256(output) != metadata.get("sha256_результата"):
        raise ValueError("Подготовленный результат повреждён.")
    if directory_sha256(state) != metadata.get("sha256_памяти"):
        raise ValueError("Подготовленная память повреждена.")
    return metadata


def _backup_current(project_dir: Path, transaction: Path) -> None:
    backup = transaction / "backup"
    if (backup / "готово").is_file():
        return
    temporary = transaction / "backup.tmp"
    _remove_within(temporary, transaction)
    temporary.mkdir()
    state_hash = directory_sha256(project_dir / "state")
    output_hash = directory_sha256(project_dir / "output")
    progress_hash = _file_sha256(project_dir / "progress.json")
    shutil.copytree(project_dir / "state", temporary / "state")
    shutil.copytree(project_dir / "output", temporary / "output")
    shutil.copy2(project_dir / "progress.json", temporary / "progress.json")
    if directory_sha256(temporary / "state") != state_hash:
        raise ValueError("Не удалось проверить резервную копию памяти.")
    if directory_sha256(temporary / "output") != output_hash:
        raise ValueError("Не удалось проверить резервную копию результатов.")
    if _file_sha256(temporary / "progress.json") != progress_hash:
        raise ValueError("Не удалось проверить резервную копию progress.json.")
    _write_text_atomic(temporary / "готово", "да\n")
    _remove_within(backup, transaction)
    temporary.replace(backup)


def _replace_directory(source: Path, target: Path, transaction: Path, label: str) -> None:
    temporary = target.parent / f".{target.name}-{transaction.name}.tmp"
    _remove_within(temporary, target.parent)
    shutil.copytree(source, temporary)
    if directory_sha256(temporary) != directory_sha256(source):
        raise ValueError(f"Не удалось проверить временную копию {label}.")
    previous = transaction / f"до-{label}"
    if target.exists():
        if target.is_symlink() or previous.exists():
            _remove_within(temporary, target.parent)
            raise ValueError(f"Нельзя безопасно заменить {label}.")
        target.replace(previous)
    temporary.replace(target)


def _mark_step(transaction: Path, step: str) -> None:
    _write_text_atomic(transaction / step, "да\n")


def _publish_state(project_dir: Path, transaction: Path, metadata: dict) -> None:
    marker = transaction / "state"
    target = project_dir / "state"
    expected = metadata["sha256_памяти"]
    if target.is_dir() and directory_sha256(target) == expected:
        if not marker.exists():
            _mark_step(transaction, "state")
        return
    if marker.exists():
        raise ValueError("Опубликованная память не совпадает с транзакцией.")
    _replace_directory(transaction / "new-state", target, transaction, "память")
    _mark_step(transaction, "state")


def _publish_output(project_dir: Path, transaction: Path, metadata: dict) -> None:
    marker = transaction / "output"
    chapter_name = metadata["глава"]
    source = transaction / "new-output" / chapter_name
    target = project_dir / "output" / chapter_name
    expected = metadata["sha256_результата"]
    if target.is_file() and _file_sha256(target) == expected:
        if not marker.exists():
            _mark_step(transaction, "output")
        return
    if marker.exists():
        raise ValueError("Опубликованный результат не совпадает с транзакцией.")
    temporary = target.parent / f".{chapter_name}-{transaction.name}.tmp"
    _remove_within(temporary, target.parent)
    shutil.copy2(source, temporary)
    if _file_sha256(temporary) != expected:
        raise ValueError("Не удалось проверить временную копию результата.")
    temporary.replace(target)
    _mark_step(transaction, "output")


def _publish_progress(project_dir: Path, transaction: Path, metadata: dict) -> None:
    target = project_dir / "progress.json"
    next_progress = _load_json(
        transaction / "next-progress.json", "следующую контрольную точку"
    )
    next_progress["sha256_результата"] = metadata["sha256_результата"]
    next_progress["sha256_памяти"] = metadata["sha256_памяти"]
    write_json_atomic(target, next_progress)
    _mark_step(transaction, "progress")


def _restore_backup(project_dir: Path, transaction: Path) -> None:
    backup = transaction / "backup"
    if not (backup / "готово").is_file():
        raise ValueError("Резервная копия транзакции не готова.")
    for name in ("state", "output"):
        failed = transaction / f"несогласованный-{name}"
        _remove_within(failed, transaction)
        target = project_dir / name
        if target.exists():
            target.replace(failed)
        shutil.copytree(backup / name, target)
    _write_bytes_atomic(
        project_dir / "progress.json", (backup / "progress.json").read_bytes()
    )
    _write_text_atomic(transaction / "отменено", "да\n")


def commit_transaction(
    project_dir: Path,
    transaction_dir: Path,
    interrupt_after: str | None = None,
) -> None:
    project_dir = project_dir.resolve()
    for name in ("work", "state", "output"):
        _project_directory(project_dir, name)
    if _is_link(project_dir / "progress.json"):
        raise ValueError("Путь progress.json внутри проекта небезопасен.")
    transaction = _transaction_for_project(project_dir, transaction_dir)
    if interrupt_after not in {None, "state", "output"}:
        raise ValueError("Неизвестная точка прерывания транзакции.")
    if not (transaction / "готово-к-фиксации").is_file():
        raise ValueError("Транзакция не готова к фиксации.")
    if (transaction / "отменено").exists():
        raise ValueError("Транзакция отменена после восстановления резервной копии.")
    try:
        metadata = _validate_prepared(transaction)
        _backup_current(project_dir, transaction)
        _publish_state(project_dir, transaction, metadata)
        if interrupt_after == "state":
            return
        _publish_output(project_dir, transaction, metadata)
        if interrupt_after == "output":
            return
        _publish_progress(project_dir, transaction, metadata)
        if _file_sha256(project_dir / "output" / metadata["глава"]) != metadata[
            "sha256_результата"
        ]:
            raise ValueError("Контрольная сумма опубликованного результата не совпала.")
        if directory_sha256(project_dir / "state") != metadata["sha256_памяти"]:
            raise ValueError("Контрольная сумма опубликованной памяти не совпала.")
        _write_text_atomic(transaction / "завершено", "да\n")
    except (OSError, ValueError) as error:
        if (transaction / "backup" / "готово").is_file():
            _restore_backup(project_dir, transaction)
            raise ValueError(
                f"Транзакция несогласована; опубликованные данные восстановлены: {error}"
            ) from error
        raise


def recover_transaction(project_dir: Path) -> None:
    project_dir = project_dir.resolve()
    transactions = _transactions_directory(project_dir)
    if not transactions.exists():
        return
    ready = []
    for transaction in transactions.iterdir():
        if _is_link(transaction):
            raise ValueError("каталог транзакции небезопасен.")
        if not transaction.is_dir():
            continue
        transaction = _transaction_directory(transactions, transaction)
        if not (transaction / "готово-к-фиксации").is_file():
            _remove_within(transaction, transactions)
        elif not (transaction / "завершено").is_file() and not (transaction / "отменено").is_file():
            ready.append(transaction)
    if len(ready) > 1:
        raise ValueError("Найдено несколько незавершённых готовых транзакций.")
    if ready:
        commit_transaction(project_dir, ready[0])


def _published_output(project_dir: Path, chapter_name: str) -> Path:
    output = _project_directory(project_dir, "output")
    result = output / _chapter_name(chapter_name)
    if (
        _is_link(result)
        or not result.is_file()
        or result.resolve().parent != output.resolve()
    ):
        raise ValueError(
            f"Опубликованный результат главы «{chapter_name}» отсутствует или небезопасен."
        )
    return result


def _completed_transaction(project_dir: Path, chapter_name: str) -> Path:
    transactions = _transactions_directory(project_dir)
    transaction = transactions / hashlib.sha256(chapter_name.encode("utf-8")).hexdigest()
    if _is_link(transaction):
        raise ValueError("каталог транзакции небезопасен.")
    if not transaction.exists():
        raise ValueError(f"Транзакция главы «{chapter_name}» отсутствует.")
    transaction = _transaction_directory(transactions, transaction)
    marker = transaction / "завершено"
    if _is_link(marker) or not marker.is_file():
        raise ValueError(
            f"Завершённый маркер транзакции главы «{chapter_name}» отсутствует."
        )
    return transaction


def check_completed_chapter(project_dir: Path, chapter_name: str) -> list[str]:
    project_dir = project_dir.resolve()
    try:
        chapter_name = _chapter_name(chapter_name)
        output = _published_output(project_dir, chapter_name)
        transaction = _completed_transaction(project_dir, chapter_name)
        metadata = _validate_prepared(transaction)
    except (OSError, ValueError) as error:
        return [str(error)]
    if metadata.get("глава") != chapter_name:
        return [f"Описание транзакции не относится к главе «{chapter_name}»."]
    if _file_sha256(output) != metadata.get("sha256_результата"):
        return [f"Контрольная сумма опубликованного результата главы «{chapter_name}» не совпадает."]
    return []


def check_consistency(project_dir: Path) -> list[str]:
    project_dir = project_dir.resolve()
    try:
        state = load_progress(project_dir)
    except ValueError as error:
        return [str(error)]
    errors = _progress_schema_errors(state, require_hashes=True)
    try:
        _project_directory(project_dir, "output")
    except ValueError as error:
        errors.append(str(error))
    if errors:
        return errors
    chapter_name = state.get("последняя_готовая_глава")
    if chapter_name:
        try:
            chapter_name = _chapter_name(chapter_name)
        except ValueError as error:
            return [str(error)]
        chapter_errors = check_completed_chapter(project_dir, chapter_name)
        errors.extend(chapter_errors)
        if not chapter_errors:
            output = _published_output(project_dir, chapter_name)
            expected_output = state.get("sha256_результата")
            if _file_sha256(output) != expected_output:
                errors.append("Контрольная сумма опубликованного результата не совпадает.")
        expected_state = state.get("sha256_памяти")
        try:
            actual_state = directory_sha256(project_dir / "state")
        except ValueError as error:
            errors.append(str(error))
        else:
            if actual_state != expected_state:
                errors.append("Контрольная сумма памяти не совпадает.")
    return errors


def finish_book(project_dir: Path) -> None:
    project_dir = project_dir.resolve()
    work_dir = _project_directory(project_dir, "work")
    state = load_progress(project_dir)
    if state.get("статус_книги") == "ошибка" or state.get("ошибка"):
        raise ValueError("Книгу нельзя завершить при зафиксированной ошибке.")
    if state.get("этап") != "готово" or not state.get("текущая_глава"):
        raise ValueError("Текущая глава не завершена.")
    manifest_path = work_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = _load_json(manifest_path, "манифест глав")
        chapters = manifest.get("главы")
        if not isinstance(chapters, list):
            raise ValueError("Манифест не содержит список глав.")
        if any(
            not isinstance(chapter, dict) or not isinstance(chapter.get("имя"), str)
            for chapter in chapters
        ):
            raise ValueError("Манифест содержит некорректную главу.")
        names = [chapter["имя"] for chapter in chapters]
        last_ready = state.get("последняя_готовая_глава")
        if not names or last_ready not in names or names[-1] != last_ready:
            raise ValueError("В книге остались необработанные главы.")
        for chapter_name in names:
            errors = check_completed_chapter(project_dir, chapter_name)
            if errors:
                raise ValueError(f"Глава «{chapter_name}» не готова: {' '.join(errors)}")
    errors = check_consistency(project_dir)
    if errors:
        raise ValueError("Проект несогласован: " + " ".join(errors))
    state["статус_книги"] = "готово"
    write_json_atomic(project_dir / "progress.json", state)
    (work_dir / "active.json").unlink(missing_ok=True)


def _restart_paths(project_dir: Path) -> list[Path]:
    paths = [project_dir / "output", project_dir / "state", project_dir / "progress.json"]
    work = _project_directory(project_dir, "work")
    if work.is_dir():
        paths.extend(path for path in work.iterdir() if path.name != "restarts")
    return paths


def restart_project(project_dir: Path, confirmed: bool = False) -> list[Path] | Path:
    project_dir = project_dir.resolve()
    for name in ("output", "state", "work"):
        _project_directory(project_dir, name)
    affected = _restart_paths(project_dir)
    if not confirmed:
        return affected
    for path in affected:
        candidates = [path]
        if path.is_dir() and not _is_link(path):
            candidates.extend(path.rglob("*"))
        if any(_is_link(candidate) for candidate in candidates):
            raise ValueError(f"Нельзя безопасно перезапустить проект: {path.name} небезопасен.")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = project_dir / "work" / "restarts" / timestamp
    backup.mkdir(parents=True)
    output_hash = directory_sha256(project_dir / "output")
    state_hash = directory_sha256(project_dir / "state")
    progress_hash = _file_sha256(project_dir / "progress.json")
    shutil.copytree(project_dir / "output", backup / "output")
    shutil.copytree(project_dir / "state", backup / "state")
    shutil.copy2(project_dir / "progress.json", backup / "progress.json")
    work_backup = backup / "work"
    work_backup.mkdir()
    for path in affected[3:]:
        target = work_backup / path.name
        if path.is_dir():
            shutil.copytree(path, target)
        elif path.is_file():
            shutil.copy2(path, target)
    if directory_sha256(backup / "output") != output_hash:
        raise ValueError("Не удалось проверить резервную копию результатов.")
    if directory_sha256(backup / "state") != state_hash:
        raise ValueError("Не удалось проверить резервную копию памяти.")
    if _file_sha256(backup / "progress.json") != progress_hash:
        raise ValueError("Не удалось проверить резервную копию progress.json.")
    for path in affected:
        _remove_within(path, project_dir)
    initialize_project(project_dir)
    return backup
