#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "book-translator" / "scripts"))
from progress import check_consistency


ALLOW = {"continue": True, "suppressOutput": True}


def find_project(start: Path) -> Path | None:
    try:
        current = start.resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if (candidate / "work" / "active.json").is_file():
            return candidate
    return None


def block(reason: str) -> dict:
    return {"decision": "block", "reason": reason}


def evaluate(event: dict) -> dict:
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else Path.cwd()
    project = find_project(Path(cwd))
    if project is None:
        return ALLOW

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
    result = evaluate(event if isinstance(event, dict) else {})
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
