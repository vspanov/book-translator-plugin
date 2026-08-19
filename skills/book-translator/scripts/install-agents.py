import argparse
import os
import sys
import tempfile
from pathlib import Path


EXPECTED_NAMES = (
    "translator.toml",
    "verifier.toml",
    "editor.toml",
    "state-updater.toml",
)
REPARSE_POINT = 0x0400


def _reject_traversal(path: Path) -> None:
    if ".." in path.parts:
        raise ValueError("Путь содержит запрещённый переход к родительскому каталогу.")


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    return bool(attributes & REPARSE_POINT)


def _check_existing_components(path: Path) -> Path:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.exists() or current.is_symlink():
            if _is_reparse(current):
                raise ValueError("Целевой путь не может содержать ссылку или точку повторной обработки.")
    return absolute


def _validate_target_directory(target_dir: Path, create: bool) -> Path:
    _reject_traversal(target_dir)
    target = _check_existing_components(target_dir)
    if target.exists() and not target.is_dir():
        raise ValueError("Целевой путь должен быть каталогом.")
    if create:
        target.mkdir(parents=True, exist_ok=True)
        _check_existing_components(target)
    return target


def _source_files(source_dir: Path) -> dict[str, bytes]:
    _reject_traversal(source_dir)
    if not source_dir.is_dir() or _is_reparse(source_dir):
        raise ValueError("Каталог исходных агентов недоступен или небезопасен.")
    entries = {entry.name for entry in source_dir.iterdir()}
    if entries != set(EXPECTED_NAMES):
        raise ValueError("Состав исходного каталога должен содержать ровно четыре ожидаемых TOML-файла.")
    files = {}
    for name in EXPECTED_NAMES:
        path = source_dir / name
        if not path.is_file() or _is_reparse(path):
            raise ValueError("Исходный файл агента должен быть обычным файлом.")
        files[name] = path.read_bytes()
    return files


def _status(target_dir: Path, name: str, source: bytes) -> str:
    target = target_dir / name
    if not target.exists() and not target.is_symlink():
        return "создать"
    if _is_reparse(target):
        raise ValueError("Целевой файл не может быть ссылкой или точкой повторной обработки.")
    if not target.is_file():
        raise ValueError("Целевой файл агента должен быть обычным файлом.")
    return "совпадает" if target.read_bytes() == source else "отличается"


def plan_install(source_dir: Path, target_dir: Path) -> list[dict]:
    sources = _source_files(Path(source_dir))
    target = _validate_target_directory(Path(target_dir), create=False)
    return [{"name": name, "status": _status(target, name, sources[name])} for name in EXPECTED_NAMES]


def _replace_atomically(target: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".book-translator-", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def install_agents(source_dir: Path, target_dir: Path, confirmed: bool, overwrite: set[str]) -> list[str]:
    unknown = set(overwrite) - set(EXPECTED_NAMES)
    if unknown:
        raise ValueError("Запрошена перезапись неизвестного файла агента.")
    sources = _source_files(Path(source_dir))
    target = _validate_target_directory(Path(target_dir), create=False)
    plan = [{"name": name, "status": _status(target, name, sources[name])} for name in EXPECTED_NAMES]
    if not confirmed:
        return []
    changed = {item["name"] for item in plan if item["status"] == "отличается"}
    if changed - set(overwrite):
        raise ValueError("Изменённый пользовательский файл нельзя перезаписать без явного разрешения.")
    target = _validate_target_directory(Path(target_dir), create=True)
    installed = []
    for item in plan:
        if item["status"] == "совпадает":
            continue
        destination = target / item["name"]
        if _is_reparse(destination):
            raise ValueError("Целевой файл не может быть ссылкой или точкой повторной обработки.")
        _replace_atomically(destination, sources[item["name"]])
        installed.append(item["name"])
    return installed


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, "Ошибка аргументов. Проверьте параметры команды.\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Безопасная установка четырёх агентов переводчика книг.")
    parser.add_argument("--source", help="Каталог с четырьмя TOML-файлами агентов.")
    parser.add_argument("--target", help="Каталог для установки агентов.")
    parser.add_argument("--plan", action="store_true", help="Показать план без записи файлов.")
    parser.add_argument("--confirm", action="store_true", help="Подтвердить установку.")
    parser.add_argument("--overwrite", action="append", default=[], metavar="ФАЙЛ", help="Явно разрешить перезапись файла.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.source or not args.target:
        parser.error("укажите --source и --target")
    if args.overwrite and not args.confirm:
        parser.error("--overwrite допустим только вместе с --confirm")
    try:
        if args.confirm:
            installed = install_agents(Path(args.source), Path(args.target), True, set(args.overwrite))
            print(f"Установлено файлов: {len(installed)}")
        else:
            print("Файл\tСтатус")
            for item in plan_install(Path(args.source), Path(args.target)):
                print(f"{item['name']}\t{item['status']}")
        return 0
    except ValueError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("Ошибка файловой системы при проверке или установке агентов.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
