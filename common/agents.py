"""Specialist subagent management for the AI upgrade flow.

The plugin ships a set of Claude Code subagent definitions under ``agents/``
(``odoo-upg-*.md``). For the ``claude`` CLI these are synced into the user's
``~/.claude/agents/`` directory before the agent runs, so the sandboxed Claude
Code session discovers them and the upgrade Lead can delegate via the ``Task``
tool / ``Workflow`` ``agentType``.

This module is intentionally free of any ``odev`` imports so its logic can be
unit-tested without the full framework. The command wraps it for logging.
"""

from pathlib import Path


# Only files whose name starts with this prefix are owned/managed by the plugin.
# The sync never reads, overwrites, or deletes anything outside this namespace.
SPECIALIST_PREFIX = "odoo-upg-"

# The CLI for which file-based subagents are meaningful. Other CLIs receive the
# specialist guidance inline in the rendered prompt instead.
SUBAGENT_CLI = "claude"


def agents_source_dir() -> Path:
    """Return the plugin-shipped ``agents/`` directory (source of truth)."""
    # common/agents.py -> common/ -> plugin root -> agents/
    return Path(__file__).resolve().parent.parent / "agents"


def specialist_agent_files(src: Path | None = None) -> list[Path]:
    """Return the plugin-owned specialist definition files (``odoo-upg-*.md``)."""
    source = src or agents_source_dir()
    if not source.is_dir():
        return []
    return sorted(p for p in source.glob(f"{SPECIALIST_PREFIX}*.md") if p.is_file())


def sync_specialist_agents(
    cli: str,
    dest: Path | None = None,
    src: Path | None = None,
) -> list[Path]:
    """Sync the plugin's specialist subagents into the Claude agents directory.

    Copies every ``odoo-upg-*.md`` from the plugin's ``agents/`` dir into
    ``dest`` (defaults to ``~/.claude/agents``), overwriting plugin-owned files
    so they always match the shipped version. Files outside the
    :data:`SPECIALIST_PREFIX` namespace are never touched.

    :param cli: the resolved AI CLI. Sync is a no-op for any CLI other than
        :data:`SUBAGENT_CLI` (``"claude"``) — those receive inline guidance.
    :param dest: target agents directory (defaults to ``~/.claude/agents``).
    :param src: source directory (defaults to the plugin ``agents/`` dir).
    :returns: the list of destination paths written (empty if nothing synced).
    """
    if cli != SUBAGENT_CLI:
        return []

    sources = specialist_agent_files(src)
    if not sources:
        return []

    target_dir = dest or (Path.home() / ".claude" / "agents")
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for source_file in sources:
        # Defensive: never write anything outside our namespace.
        if not source_file.name.startswith(SPECIALIST_PREFIX):
            continue
        target = target_dir / source_file.name
        target.write_text(source_file.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(target)

    return written
