"""Temporarily raise the launched Claude Code effort level for an upgrade run.

Claude Code reads ``effortLevel`` (``low``/``medium``/``high``/``xhigh``) from
``~/.claude/settings.json`` at session start. The odev AI sandbox mounts the
user's *real* ``~/.claude`` directory, but its launch command (the read-only
``odev-plugin-ai`` sibling) never passes ``--effort`` — so the user's global
``effortLevel`` governs every upgrade agent. A user who keeps ``effortLevel:
low`` for everyday coding therefore gets a low-effort migration agent.

Module migrations are complex, so the plugin raises ``effortLevel`` for the
duration of the run and **restores the user's original value afterwards**.
This is the same "write ~/.claude on the host before launch" pattern used to
sync the specialist agents.

This module is intentionally free of any ``odev`` imports so its logic can be
unit-tested without the full framework.
"""

import json
from pathlib import Path


# effortLevel values Claude Code accepts in settings.json ("max"/"ultracode"
# are session-only and cannot be persisted here).
VALID_EFFORTS = ("low", "medium", "high", "xhigh")

_MISSING = object()


def settings_path(home: Path | None = None) -> Path:
    """Return the path to the Claude Code user settings file."""
    return (home or Path.home()) / ".claude" / "settings.json"


def apply_effort_override(level: str, path: Path | None = None) -> dict | None:
    """Set ``effortLevel`` to ``level`` in settings.json (read-modify-write).

    Preserves every other key. Returns a restore token to pass to
    :func:`restore_effort_override`, or ``None`` when nothing was changed
    (invalid level, unreadable/non-dict settings, or already at ``level``).
    """
    if level not in VALID_EFFORTS:
        return None

    target = path or settings_path()
    data: dict = {}
    had_file = target.exists()
    if had_file:
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None  # never clobber an unreadable / non-JSON settings file
        if not isinstance(loaded, dict):
            return None
        data = loaded

    previous = data.get("effortLevel", _MISSING)
    if previous == level:
        return None  # already there — no-op, nothing to restore

    data["effortLevel"] = level
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    return {
        "path": str(target),
        "had_file": had_file,
        "had_key": previous is not _MISSING,
        "old_value": None if previous is _MISSING else previous,
        "applied": level,
    }


def restore_effort_override(token: dict | None) -> None:
    """Restore the ``effortLevel`` captured in ``token`` (best-effort).

    Re-reads settings.json (which may have been augmented in the meantime, e.g.
    with ``trustedDirectories`` by the sandbox) so only ``effortLevel`` is
    touched. Removes the key entirely if it was absent before the override.
    """
    if not token:
        return

    target = Path(token["path"])
    try:
        data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (ValueError, OSError):
        return
    if not isinstance(data, dict):
        return

    if token.get("had_key"):
        data["effortLevel"] = token.get("old_value")
    else:
        data.pop("effortLevel", None)

    try:
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
