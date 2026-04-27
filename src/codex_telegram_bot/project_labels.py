from __future__ import annotations

from pathlib import Path
from typing import Optional


def render_project_display_name(path: Optional[Path]) -> str:
    if path is None:
        return "не выбран"
    resolved = path.expanduser().resolve()
    project_name = resolved.name.strip()
    if not project_name:
        return str(resolved)
    parent_name = resolved.parent.name.strip()
    if not parent_name:
        return project_name
    return f"{parent_name}/{project_name}"
