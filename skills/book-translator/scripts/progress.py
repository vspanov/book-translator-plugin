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
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
