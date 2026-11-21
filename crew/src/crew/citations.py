"""Advisory checks for local file citations in worker reviews."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from crew.common import is_secret_name, is_within


# Quoted paths and Markdown destinations may contain spaces. Bare citations require a line.
_REFERENCES = re.compile(
    r"`(?P<quoted>[^`\n]+)`|\]\(<?(?P<link>[^)<>\n]+)>?\)"
    r"|(?<![\w:/\\])(?P<bare>(?:[A-Za-z]:)?[\w./\\-]+:\d+)(?![\w:])"
)


def unverified(root: Path, text: str) -> list[str]:
    warnings = []
    line_counts: dict[Path, int] = {}
    seen: set[str] = set()
    for match in _REFERENCES.finditer(text):
        ref = match.group(match.lastgroup).strip()
        if ref in seen:
            continue
        seen.add(ref)
        local = ref
        if "://" in ref:
            if not ref.lower().startswith("file:///"):
                continue
            uri = urlsplit(ref)
            local = unquote(uri.path) + ("#" + uri.fragment if uri.fragment else "")
        if os.name == "nt" and re.match(r"^/[a-zA-Z]:/", local):
            local = local[1:]
        loc = re.fullmatch(r"(.+?)(?::(\d+)|#L(\d+))?", local)
        if not loc:
            continue
        name, colon_line, anchor_line = loc.groups()
        line = colon_line or anchor_line
        path = Path(name)
        if name.startswith(("//", "\\\\", "#", "--")) or name.endswith(("/", "\\")):
            continue
        if not line and not path.suffix:
            continue
        if ":" in name[len(path.drive):] or path.drive and path.drive.lower() != root.drive.lower():
            continue
        try:
            candidate = root / path
            resolved = candidate.resolve()
            if not is_within(resolved, root):
                continue
            parts = (*path.parts, *resolved.relative_to(root).parts)
            if any(p.lower() in (".git", ".agent-runs") or is_secret_name(p) for p in parts):
                continue
            exists = resolved.is_file()
            if exists and line:
                if resolved not in line_counts:
                    with resolved.open("rb") as source:
                        line_counts[resolved] = sum(1 for _ in source)
                exists = 1 <= int(line) <= line_counts[resolved]
        except (OSError, ValueError, RuntimeError):
            exists = False
        if not exists:
            warnings.append(ref)
    return warnings
