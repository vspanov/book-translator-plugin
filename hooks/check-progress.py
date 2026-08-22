#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.dont_write_bytecode = True
PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "book-translator" / "scripts"))
from progress import PROJECT_MARKER_NAME, check_consistency, has_project_identity, is_unsafe_link


ALLOW = {"continue": True, "suppressOutput": True}


def block(reason: str) -> dict:
    return {"decision": "block", "reason": reason}


def find_project(start: Path) -> Path | None:
    for candidate in (start.resolve(), *start.resolve().parents):
        identity = candidate / PROJECT_MARKER_NAME
        if is_unsafe_link(identity) or not identity.is_file():
            continue
        try:
            marker = json.loads(identity.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not has_project_identity(marker):
            continue
        active = candidate / "work" / "active.json"
        if active.is_file() or is_unsafe_link(active): return candidate
    return None


def evaluate(event: dict) -> dict:
    start = Path(event.get("cwd", Path.cwd())) if isinstance(event.get("cwd", str(Path.cwd())), str) else Path.cwd()
    project = find_project(start)
    if project is None: return ALLOW
    active = project / "work" / "active.json"
    if is_unsafe_link(active) or not active.is_file(): return block("Маркер активного перевода небезопасен.")
    try:
        marker = json.loads(active.read_text(encoding="utf-8"))
        progress = json.loads((project / "progress.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return block("Не удалось прочитать состояние активного перевода.")
    if not has_project_identity(marker) or marker.get("проект") != str(project.resolve()):
        return block("Маркер активного перевода относится к другому или устаревшему проекту.")
    if progress.get("статус_книги") == "ошибка" and progress.get("ошибка"):
        return ALLOW
    if progress.get("статус_книги") == "готово":
        errors = check_consistency(project)
        return block("Завершение несогласовано: " + " ".join(errors)) if errors else ALLOW
    if progress.get("статус_книги") == "ожидает-одобрения":
        return ALLOW
    return block(
        f"Перевод нельзя завершить: {progress.get('текущий_файл') or 'файл'} "
        f"остановлен на этапе {progress.get('этап') or 'неизвестно'}."
    )


def main() -> None:
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    try: event = json.load(sys.stdin)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError): event = {}
    json.dump(evaluate(event if isinstance(event, dict) else {}), sys.stdout, ensure_ascii=False)


if __name__ == "__main__": main()
