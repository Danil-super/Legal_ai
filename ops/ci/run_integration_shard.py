"""Run every configured test file exactly once across isolated CI workers."""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def select_files(root: Path, index: int, count: int) -> list[str]:
    if type(index) is not int or type(count) is not int or not 0 <= index < count <= 32:
        raise ValueError("invalid integration shard")
    options = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    paths = options.get("testpaths")
    patterns = options.get("python_files", ["test_*.py", "*_test.py"])
    if (not isinstance(paths, list) or not paths or not isinstance(patterns, list)
            or not patterns or any(not isinstance(item, str) or not item for item in patterns)):
        raise ValueError("complete pytest roots and file patterns are required")
    files = []
    for name in paths:
        if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("pytest roots must stay inside the checkout")
        directory = root / name
        if not directory.is_dir() or not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError("configured pytest directory is missing or outside the checkout")
        selected = sorted(path for path in directory.rglob("*.py")
                          if any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns))
        for path in selected:
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("test file points outside the checkout")
            files.append(path.relative_to(root).as_posix())
    if len(set(files)) != len(files):
        raise ValueError("overlapping pytest roots would repeat tests")
    # Preserve canonical root order: cross-service fixture imports need Core collected first.
    shard = files[index::count]
    if not shard:
        raise ValueError("an empty integration shard cannot pass")
    return shard


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    arguments = parser.parse_args(argv)
    try:
        files = select_files(ROOT, arguments.index, arguments.count)
    except (OSError, ValueError, KeyError, TypeError):
        parser.error("Cannot select a complete integration shard")
    # Replace the selector, so the unchanged outer timeout also owns the actual pytest process.
    os.execv(sys.executable, [sys.executable, "-m", "pytest", "-vv", *files])


if __name__ == "__main__":
    main()
