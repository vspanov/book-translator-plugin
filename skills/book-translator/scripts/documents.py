from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


SUPPORTED_SUFFIXES = {".docx", ".pages"}
EXCLUDED_DIRECTORIES = {"output", "state", "work"}


def natural_key(path: Path) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", path.name)
    )


def discover_chapters(project_dir: Path) -> list[Path]:
    project_dir = project_dir.resolve()
    input_dir = project_dir / "input"
    root_files = [
        path
        for path in project_dir.iterdir()
        if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES
    ]
    input_files = [] if not input_dir.is_dir() else [
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES
    ]
    if root_files and input_files:
        raise ValueError(
            "Поддерживаемые документы найдены одновременно в input/ и корне проекта."
        )
    chapters = input_files or root_files
    folded = [path.stem.casefold() for path in chapters]
    if len(folded) != len(set(folded)):
        raise ValueError("Имена глав неоднозначны с учётом регистра.")
    keys = [natural_key(path) for path in chapters]
    if len(keys) != len(set(keys)):
        raise ValueError("Порядок глав неоднозначен из-за совпадающих числовых частей.")
    if not chapters:
        raise ValueError("Поддерживаемые главы .docx или .pages не найдены.")
    return sorted(chapters, key=natural_key)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(project_dir: Path, path: Path) -> str:
    project_dir = project_dir.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_dir).as_posix()
    except ValueError as error:
        raise ValueError("Исходная глава должна находиться внутри проекта.") from error


def _write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def build_manifest(project_dir: Path, chapters: list[Path]) -> dict:
    project_dir = project_dir.resolve()
    ordered = sorted((path.resolve() for path in chapters), key=natural_key)
    entries = []
    for number, chapter in enumerate(ordered, start=1):
        relative = _relative_path(project_dir, chapter)
        entries.append(
            {
                "номер": number,
                "имя": chapter.name,
                "путь": relative,
                "sha256": file_sha256(chapter),
            }
        )

    parents = {Path(entry["путь"]).parent.as_posix() for entry in entries}
    source = next(iter(parents), ".") if len(parents) == 1 else "смешанный"
    manifest = {"версия": 1, "источник": source, "главы": entries}
    _write_manifest(project_dir / "work" / "manifest.json", manifest)
    return manifest


def _manifest_paths(manifest: dict) -> list[str]:
    chapters = manifest.get("главы")
    if not isinstance(chapters, list):
        raise ValueError("Манифест не содержит корректный список глав.")
    paths = []
    for chapter in chapters:
        if not isinstance(chapter, dict) or not isinstance(chapter.get("путь"), str):
            raise ValueError("Манифест содержит главу без корректного пути.")
        paths.append(Path(chapter["путь"]).as_posix())
    return paths


def verify_manifest(project_dir: Path, manifest: dict) -> list[str]:
    project_dir = project_dir.resolve()
    try:
        manifest_paths = _manifest_paths(manifest)
    except ValueError as error:
        return [str(error)]

    try:
        current = discover_chapters(project_dir)
    except ValueError as error:
        if "не найдены" in str(error):
            current = []
        else:
            return [str(error)]

    current_paths = {_relative_path(project_dir, path): path for path in current}
    errors = []
    for chapter in manifest["главы"]:
        relative = Path(chapter["путь"]).as_posix()
        path = current_paths.get(relative)
        if path is None:
            errors.append(f"Глава «{chapter.get('имя', relative)}» удалена.")
            continue
        expected = chapter.get("sha256")
        if not isinstance(expected, str) or file_sha256(path) != expected:
            errors.append(f"Глава «{path.name}» изменена.")

    current_order = [_relative_path(project_dir, path) for path in current]
    expected_order = manifest_paths
    new_paths = [path for path in current_order if path not in expected_order]
    if expected_order:
        last_key = natural_key(Path(expected_order[-1]))
        if any(natural_key(Path(path)) <= last_key for path in new_paths):
            errors.append(
                "Новые главы должны добавляться только после последней зафиксированной главы."
            )
    return errors


def _output_suffix(chapter: Path, output_format: str) -> str | None:
    selected = output_format.strip().casefold()
    if selected in {"как в оригинале", "оригинал", "original"}:
        return chapter.suffix.casefold()
    if selected in {"docx", ".docx"}:
        return ".docx"
    if selected in {"pages", ".pages"}:
        return ".pages"
    return None


def _confirmed_outputs(
    project_dir: Path, chapters: list[Path], output_format: str
) -> tuple[set[str], str | None, str | None]:
    progress_path = project_dir / "progress.json"
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set(), None, None
    if not isinstance(progress, dict):
        return set(), None, None

    last_ready = progress.get("последняя_готовая_глава")
    if last_ready is None:
        return set(), None, None
    ordered = sorted(chapters, key=natural_key)
    try:
        last_index = next(
            index for index, chapter in enumerate(ordered)
            if chapter.name == last_ready
        )
    except StopIteration:
        return set(), None, None
    confirmed = set()
    last_name = None
    for chapter in ordered[: last_index + 1]:
        suffix = _output_suffix(chapter, output_format)
        if suffix is None:
            return set(), None, None
        name = f"{chapter.stem}{suffix}"
        confirmed.add(name.casefold())
        last_name = name
    result_hash = progress.get("sha256_результата")
    if result_hash is not None and not isinstance(result_hash, str):
        return set(), None, None
    return confirmed, last_name.casefold() if last_name else None, result_hash


def check_output_conflicts(
    project_dir: Path, chapters: list[Path], output_format: str
) -> list[str]:
    project_dir = project_dir.resolve()
    output_dir = project_dir / "output"
    names = {}
    errors = []
    confirmed, last_confirmed, result_hash = _confirmed_outputs(
        project_dir, chapters, output_format
    )
    for chapter in sorted(chapters, key=natural_key):
        suffix = _output_suffix(chapter, output_format)
        if suffix is None:
            errors.append(f"Неподдерживаемый выходной формат: {output_format}.")
            break
        name = f"{chapter.stem}{suffix}"
        folded = name.casefold()
        previous = names.get(folded)
        if previous is not None:
            errors.append(
                f"Главы «{previous.name}» и «{chapter.name}» создают одно имя результата «{name}»."
            )
        else:
            names[folded] = chapter

        if output_dir.exists():
            existing = next(
                (path for path in output_dir.iterdir() if path.name.casefold() == folded),
                None,
            )
            if existing is not None and existing.name.casefold() not in confirmed:
                errors.append(
                    f"Выходной файл «{existing.name}» уже существует и не подтверждён текущей контрольной точкой."
                )
            elif (
                existing is not None
                and existing.name.casefold() == last_confirmed
                and result_hash is not None
                and file_sha256(existing) != result_hash
            ):
                errors.append(
                    f"Выходной файл «{existing.name}» не совпадает с контрольной суммой контрольной точки."
                )
    return errors
