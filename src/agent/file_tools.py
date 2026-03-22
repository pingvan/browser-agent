"""file_tools — sandboxed file I/O for the agent workspace.

All file operations are restricted to the ``./agent-workspace/`` directory
relative to the process working directory. This prevents the agent from
reading or writing arbitrary files on the host.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

WORKSPACE_DIR = Path("agent-workspace")

# Hard limit on file size the agent can write (256 KB).
_MAX_FILE_SIZE = 256 * 1024


def _resolve_safe(file_name: str) -> Path:
    """Resolve *file_name* inside the workspace, raising on escape attempts."""
    if not file_name or file_name.strip() == "":
        raise ValueError("file_name must not be empty")
    # Block absolute paths and parent traversal
    if os.path.isabs(file_name) or ".." in Path(file_name).parts:
        raise ValueError(f"Invalid file name: {file_name!r}. Must be a relative path without '..'.")
    resolved = (WORKSPACE_DIR / file_name).resolve()
    workspace_resolved = WORKSPACE_DIR.resolve()
    if not str(resolved).startswith(str(workspace_resolved)):
        raise ValueError(f"Path escapes workspace: {file_name!r}")
    return resolved


def ensure_workspace() -> None:
    """Create the workspace directory if it does not exist."""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


def execute_file_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a file tool and return the result dict (sync)."""
    match name:
        case "write_file":
            return _write_file(args)
        case "replace_file":
            return _replace_file(args)
        case "read_file":
            return _read_file(args)
        case _:
            return {"success": False, "error": f"Unknown file tool: {name}"}


def _write_file(args: dict[str, Any]) -> dict[str, Any]:
    file_name: str = args.get("file_name", "")
    content: str = args.get("content", "")
    try:
        path = _resolve_safe(file_name)
        if len(content.encode()) > _MAX_FILE_SIZE:
            return {
                "success": False,
                "error": f"Content exceeds max file size ({_MAX_FILE_SIZE} bytes)",
            }
        ensure_workspace()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"success": True, "file_name": file_name, "size": len(content)}
    except (ValueError, OSError) as e:
        return {"success": False, "error": str(e)}


def _replace_file(args: dict[str, Any]) -> dict[str, Any]:
    file_name: str = args.get("file_name", "")
    old_text: str = args.get("old_text", "")
    new_text: str = args.get("new_text", "")
    try:
        path = _resolve_safe(file_name)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_name!r}"}
        current = path.read_text(encoding="utf-8")
        count = current.count(old_text)
        if count == 0:
            return {"success": False, "error": f"Text not found in {file_name!r}"}
        updated = current.replace(old_text, new_text)
        path.write_text(updated, encoding="utf-8")
        return {"success": True, "replacements_made": count}
    except (ValueError, OSError) as e:
        return {"success": False, "error": str(e)}


def _read_file(args: dict[str, Any]) -> dict[str, Any]:
    file_name: str = args.get("file_name", "")
    try:
        path = _resolve_safe(file_name)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_name!r}"}
        content = path.read_text(encoding="utf-8")
        return {"success": True, "file_name": file_name, "content": content}
    except (ValueError, OSError) as e:
        return {"success": False, "error": str(e)}
