#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.dont_write_bytecode = True
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "book-translator" / "scripts"))
from progress import check_consistency


ALLOW = {"continue": True, "suppressOutput": True}


def find_project(start: Path) -> Path | None:
    try:
        current = start.resolve()
        for candidate in (current, *current.parents):
            if (candidate / "work" / "active.json").is_file():
                return candidate
    except (OSError, ValueError):
        return None
    return None


def block(reason: str) -> dict:
    return {"decision": "block", "reason": reason}


def completion_error(project: Path, state: dict) -> str | None:
    manifest_path = project / "work" / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "Завершение не согласовано: не удалось прочитать манифест глав."
    chapters = manifest.get("главы") if isinstance(manifest, dict) else None
    if not isinstance(chapters, list) or any(
        not isinstance(chapter, dict) or not isinstance(chapter.get("имя"), str)
        for chapter in chapters
    ):
        return "Завершение не согласовано: манифест глав некорректен."
    names = [chapter["имя"] for chapter in chapters]
    if not names or state.get("последняя_готовая_глава") != names[-1]:
        return "Завершение не согласовано: в очереди остались главы."
    return None


def evaluate(event: dict) -> dict:
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else Path.cwd()
    project = find_project(Path(cwd))
    if project is None:
        return ALLOW

    try:
        marker = json.loads((project / "work" / "active.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return block("Не удалось прочитать маркер активного перевода.")
    if not isinstance(marker, dict):
        return block("Маркер активного перевода должен содержать объект.")

    progress_path = project / "progress.json"
    try:
        state = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return block("Не удалось прочитать progress.json активного перевода.")
    if not isinstance(state, dict):
        return block("progress.json активного перевода должен содержать объект.")

    if state.get("статус_книги") == "ошибка":
        if isinstance(state.get("ошибка"), str) and state["ошибка"].strip():
            return ALLOW
        return block("Ошибка активного перевода не содержит объяснения.")

    if state.get("статус_книги") == "готово":
        if type(state.get("необработанных_глав")) is not int or state["необработанных_глав"] != 0:
            return block("Завершение не согласовано: очередь глав не пуста.")
        error = completion_error(project, state)
        if error:
            return block(error)
        try:
            errors = check_consistency(project)
        except Exception:
            return block("Завершение не согласовано: не удалось проверить состояние проекта.")
        if errors:
            return block("Завершение не согласовано: " + " ".join(errors))
        return ALLOW

    chapter = state.get("текущая_глава") or "неизвестная глава"
    stage = state.get("этап") or "неизвестный этап"
    return block(
        f"Перевод нельзя завершить: {chapter} остановлена на этапе {stage}. "
        "Продолжи обязательный этап либо запиши объяснённую критическую ошибку."
    )


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        event = {}
    try:
        result = evaluate(event if isinstance(event, dict) else {})
    except Exception:
        cwd = event.get("cwd") if isinstance(event, dict) and isinstance(event.get("cwd"), str) else Path.cwd()
        result = (
            block("Не удалось проверить активный перевод.")
            if find_project(Path(cwd)) is not None
            else ALLOW
        )
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
