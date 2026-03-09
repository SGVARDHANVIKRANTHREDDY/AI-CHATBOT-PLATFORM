from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Rules:
    root: Path
    out_path: Path
    include_globs: tuple[str, ...]
    exclude_dir_names: tuple[str, ...]
    exclude_file_globs: tuple[str, ...]


def _is_excluded_dir(path: Path, rules: Rules) -> bool:
    return any(part in rules.exclude_dir_names for part in path.parts)


def _is_excluded_file(path: Path, rules: Rules) -> bool:
    rel = path.relative_to(rules.root).as_posix()
    return any(Path(rel).match(pat) for pat in rules.exclude_file_globs)


def _collect_files(rules: Rules) -> list[Path]:
    files: list[Path] = []
    for glob in rules.include_globs:
        for path in rules.root.glob(glob):
            if not path.is_file():
                continue
            if path.resolve() == rules.out_path.resolve():
                continue
            if _is_excluded_dir(path.relative_to(rules.root).parent, rules):
                continue
            if _is_excluded_file(path, rules):
                continue
            files.append(path)
    files = sorted(set(files), key=lambda p: p.relative_to(rules.root).as_posix().lower())
    return files


def _tree_lines(root: Path, rules: Rules) -> list[str]:
    lines: list[str] = ["."]

    def walk(dir_path: Path, prefix: str) -> None:
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return

        filtered: list[Path] = []
        for entry in entries:
            rel = entry.relative_to(root)
            if entry.name in rules.exclude_dir_names:
                continue
            if entry.is_dir() and _is_excluded_dir(rel, rules):
                continue
            if entry.is_file() and _is_excluded_file(entry, rules):
                continue
            # Don't expand huge/binary dirs; still show them
            filtered.append(entry)

        for i, entry in enumerate(filtered):
            is_last = i == (len(filtered) - 1)
            connector = "`-- " if is_last else "|-- "
            rel = entry.relative_to(root).as_posix()

            suffix = ""  # annotate known large/binary artifacts
            if entry.is_dir() and entry.name == "__pycache__":
                suffix = " (not inlined)"
            if entry.is_dir() and rel.startswith("chatbot/data"):
                # docs/indexes not inlined
                suffix = " (not inlined)"
            if entry.is_file() and entry.suffix in {".index", ".pkl", ".pdf"}:
                suffix = " (binary; not inlined)"

            lines.append(f"{prefix}{connector}{entry.name}{suffix}")
            if entry.is_dir():
                new_prefix = prefix + ("    " if is_last else "|   ")
                walk(entry, new_prefix)

    walk(root, "")
    return lines


def _fenced(language: str, text: str) -> str:
    # Avoid accidental fence termination
    text = text.replace("```", "``\\`")
    return f"```{language}\n{text.rstrip()}\n```\n"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_path = root / "coderead.txt"

    rules = Rules(
        root=root,
        out_path=out_path,
        include_globs=(
            "pytest.ini",
            "*.py",
            "chatbot/**/*.py",
            "chatbot/README.md",
            "chatbot/requirements.txt",
            "tests/**/*.py",
            "tools/**/*.py",
        ),
        exclude_dir_names=("__pycache__", ".git", ".venv", "venv", "env", "node_modules", "models"),
        exclude_file_globs=(
            "**/*.pyc",
            "**/*.pyo",
            "**/*.pyd",
            "**/*.so",
            "**/*.dll",
            "**/*.exe",
            "**/*.dylib",
            "**/*.zip",
            "**/*.tar",
            "**/*.gz",
            "**/*.7z",
            "**/*.pdf",
            "**/*.pkl",
            "**/*.index",
            "**/chatbot/data/**",
        ),
    )

    files = _collect_files(rules)
    tree = _tree_lines(root, rules)

    parts: list[str] = []
    parts.append("# CODE READ (Auto-generated)\n")
    parts.append(f"Workspace root: {root}\n")
    parts.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    parts.append("\nNotes:\n")
    parts.append("- Only source/config/docs are inlined.\n")
    parts.append("- Binary/large artifacts (indexes, pickles, PDFs, __pycache__) are excluded from inline content.\n\n")

    parts.append("## Folder tree\n\n")
    parts.append("```\n" + "\n".join(tree) + "\n```\n\n")

    parts.append("## Files\n\n")

    for path in files:
        rel = path.relative_to(root).as_posix()
        ext = path.suffix.lower()
        if rel == "pytest.ini":
            lang = "ini"
        elif ext == ".py":
            lang = "python"
        elif ext in {".md", ".txt"}:
            lang = "markdown"
        else:
            lang = "text"

        parts.append(f"### {rel}\n\n")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        parts.append(_fenced(lang, text))
        parts.append("\n")

    out_path.write_text("".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()
