"""Validate the shipped specialist subagent definitions (``agents/*.md``).

These tests are framework-free: they read files by path so they run without the
full ``odev`` runtime. The ``tests`` tree intentionally has no ``__init__.py`` so
pytest imports each module standalone (the plugin's own ``__init__.py`` imports
``odev`` and cannot be loaded outside the framework).
"""

import re
from pathlib import Path

import yaml


EXPECTED = {
    "odoo-upg-sha-hunter",
    "odoo-upg-render-gate",
    "odoo-upg-xml-migrator",
    "odoo-upg-knowledge-curator",
}
ALLOWED_TOOLS = {
    "Read",
    "Edit",
    "Grep",
    "Glob",
    "Bash(git:*)",
    "Bash(grep:*)",
    "Bash(rtk:*)",
    "Bash(odev:*)",
    "Bash(xmllint:*)",
}


def _plugin_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "__manifest__.py").exists():
            return parent
    raise RuntimeError("Could not locate the plugin root (no __manifest__.py found).")


def _agent_files() -> list[Path]:
    return sorted((_plugin_root() / "agents").glob("odoo-upg-*.md"))


def _parse(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("---"), f"{path.name}: missing YAML frontmatter"
    _, frontmatter, body = raw.split("---", 2)
    return yaml.safe_load(frontmatter), body


def test_exactly_the_expected_agents_ship():
    names = {_parse(p)[0]["name"] for p in _agent_files()}
    assert names == EXPECTED


def test_frontmatter_is_well_formed():
    for path in _agent_files():
        meta, _body = _parse(path)
        name = meta["name"]
        assert re.fullmatch(r"odoo-upg-[a-z-]+", name), f"{path.name}: bad name {name!r}"
        assert meta.get("description", "").strip(), f"{path.name}: empty description"
        tools = {t.strip() for t in meta["tools"].split(",")}
        unknown = tools - ALLOWED_TOOLS
        assert not unknown, f"{path.name}: unknown tools {unknown}"
        assert meta.get("model") == "inherit", f"{path.name}: expected model: inherit"


def test_sha_hunter_is_read_only():
    sha = next(p for p in _agent_files() if "sha-hunter" in p.name)
    meta, _body = _parse(sha)
    tools = {t.strip() for t in meta["tools"].split(",")}
    assert "Edit" not in tools, "sha-hunter must not have Edit (read-only)"


def test_bodies_have_no_jinja_placeholders():
    # The .md files are copied verbatim into ~/.claude/agents; they are NOT
    # Jinja-rendered, so a stray {{ ... }} would leak into the system prompt.
    for path in _agent_files():
        _meta, body = _parse(path)
        assert "{{" not in body, f"{path.name}: Jinja placeholder leaked into body"
