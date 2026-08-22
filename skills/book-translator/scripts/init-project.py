#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from progress import initialize_project


def main() -> None:
    parser = argparse.ArgumentParser(description="Инициализировать рабочий каталог book-translator")
    parser.add_argument("project", nargs="?", default=".")
    parser.add_argument("--overwrite-agent", action="append", default=[])
    arguments = parser.parse_args()
    try:
        result = initialize_project(Path(arguments.project), set(arguments.overwrite_agent))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
