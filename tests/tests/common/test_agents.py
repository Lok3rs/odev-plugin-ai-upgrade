"""Tests for ``common/agents.py`` specialist-subagent sync logic.

Loads the module by file path (importlib) so the test runs without importing
the plugin package (whose ``__init__.py`` pulls in ``odev``).
"""

import importlib.util
from pathlib import Path


def _plugin_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "__manifest__.py").exists():
            return parent
    raise RuntimeError("Could not locate the plugin root (no __manifest__.py found).")


def _load_agents_module():
    path = _plugin_root() / "common" / "agents.py"
    spec = importlib.util.spec_from_file_location("upg_agents_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


agents = _load_agents_module()

EXPECTED = {
    "odoo-upg-sha-hunter.md",
    "odoo-upg-render-gate.md",
    "odoo-upg-xml-migrator.md",
    "odoo-upg-knowledge-curator.md",
    "odoo-upg-studio-views.md",
}


def test_source_files_are_the_four_specialists():
    names = {p.name for p in agents.specialist_agent_files()}
    assert names == EXPECTED


def test_sync_writes_all_specialists_for_claude(tmp_path):
    dest = tmp_path / ".claude" / "agents"
    written = agents.sync_specialist_agents("claude", dest=dest)
    assert {p.name for p in written} == EXPECTED
    # files are byte-identical to source
    for src in agents.specialist_agent_files():
        assert (dest / src.name).read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


def test_sync_is_idempotent(tmp_path):
    dest = tmp_path / "agents"
    first = agents.sync_specialist_agents("claude", dest=dest)
    second = agents.sync_specialist_agents("claude", dest=dest)
    assert {p.name for p in first} == {p.name for p in second}
    assert {p.name for p in dest.glob("*.md")} == EXPECTED


def test_sync_is_noop_for_non_claude(tmp_path):
    dest = tmp_path / "agents"
    for cli in ("gemini", "copilot", "opencode-cli"):
        assert agents.sync_specialist_agents(cli, dest=dest) == []
    assert not dest.exists() or not list(dest.glob("*.md"))


def test_sync_never_clobbers_foreign_agents(tmp_path):
    dest = tmp_path / "agents"
    dest.mkdir(parents=True)
    foreign = dest / "my-own-agent.md"
    foreign.write_text("KEEP ME", encoding="utf-8")
    agents.sync_specialist_agents("claude", dest=dest)
    assert foreign.read_text(encoding="utf-8") == "KEEP ME"
    assert {p.name for p in dest.glob("*.md")} == EXPECTED | {"my-own-agent.md"}
