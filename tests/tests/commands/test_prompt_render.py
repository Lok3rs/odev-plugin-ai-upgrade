"""Render-contract tests for ``templates/upgrade_prompt.md.j2``.

Asserts the delegation directives appear only under the right
``(cli, yolo, ultracode)`` combinations. Framework-free: renders the template
directly with Jinja2, mirroring ``commands/upgrade.py::_build_final_prompt``.
"""

from pathlib import Path

import jinja2
import pytest


def _plugin_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "__manifest__.py").exists():
            return parent
    raise RuntimeError("Could not locate the plugin root (no __manifest__.py found).")


def _render(cli: str, yolo: bool, ultracode: bool, studio_views_dump: str | None = None) -> str:
    template_path = _plugin_root() / "templates" / "upgrade_prompt.md.j2"
    template = jinja2.Template(template_path.read_text(encoding="utf-8"))
    return template.render(
        task_id="12345",
        from_ver="17.0",
        target_ver="18.0",
        from_odoo_path="/wt/17.0",
        target_odoo_path="/wt/18.0",
        project_path="/proj",
        upgrade_instructions="",
        k_path="/knowledge",
        no_ruff=False,
        is_ps_custom=False,
        comment="",
        submodules=False,
        ultracode=ultracode,
        cli=cli,
        yolo=yolo,
        modules=[{"name": "sale_x", "path": "/proj/sale_x"}],
        studio_views_dump=studio_views_dump,
        studio_views_tables=["ir_ui_view", "ir_model_data", "ir_model", "ir_model_fields"],
    )


ROSTER = "### Specialist Agents (Delegation)"
DISABLED = "Specialist delegation is disabled without `--yolo`"
TASK_TOOL = "subagent_type: 'odoo-upg-…'"
WORKFLOW = "agentType: 'odoo-upg-…', isolation: 'worktree'"
INLINE = "#### Specialist Methodology (inline reference)"


@pytest.mark.parametrize("cli", ["claude", "gemini", "copilot", "opencode-cli"])
@pytest.mark.parametrize("ultracode", [False, True])
def test_no_yolo_disables_delegation(cli, ultracode):
    out = _render(cli, yolo=False, ultracode=ultracode)
    assert ROSTER not in out
    assert DISABLED in out
    assert TASK_TOOL not in out
    assert "agentType" not in out


@pytest.mark.parametrize("cli", ["claude", "gemini", "copilot", "opencode-cli"])
@pytest.mark.parametrize("yolo", [False, True])
@pytest.mark.parametrize("ultracode", [False, True])
def test_legacy_invoke_agent_is_gone(cli, yolo, ultracode):
    assert "invoke_agent" not in _render(cli, yolo, ultracode)


def test_claude_yolo_uses_task_delegation():
    out = _render("claude", yolo=True, ultracode=False)
    assert ROSTER in out
    assert TASK_TOOL in out
    assert "agentType" not in out
    assert INLINE not in out  # claude gets methodology via subagent bodies
    assert "task_id=12345" in out
    # per-step hooks
    assert "hand this module's view migration to `odoo-upg-xml-migrator`" in out
    assert "Delegate this hunt to `odoo-upg-sha-hunter`" in out
    assert "hand this entire gate to `odoo-upg-render-gate`" in out
    assert "the authoring & dedup to `odoo-upg-knowledge-curator`" in out


def test_claude_ultracode_uses_workflow_fanout():
    out = _render("claude", yolo=True, ultracode=True)
    assert WORKFLOW in out
    assert TASK_TOOL not in out
    assert "(one agent per module, in parallel)" in out


@pytest.mark.parametrize("cli", ["gemini", "copilot", "opencode-cli"])
def test_non_claude_yolo_gets_inline_methodology(cli):
    out = _render(cli, yolo=True, ultracode=False)
    assert ROSTER in out
    assert INLINE in out
    assert TASK_TOOL not in out
    assert "agentType" not in out
    assert "delegate via your CLI's sub-task/sub-agent mechanism" in out


STUDIO_SECTION = "#### Step 2b: Studio Views Migration"
STUDIO_SETUP = "**Studio Views (customer DB extract)**"


def test_studio_views_section_renders_with_dump():
    dump = "/dumps/studio-views/mydb/20260611-mydb.dump.4-tables.sql"
    out = _render("claude", yolo=True, ultracode=False, studio_views_dump=dump)
    assert STUDIO_SECTION in out
    assert STUDIO_SETUP in out
    assert dump in out
    assert "ir_ui_view, ir_model_data, ir_model, ir_model_fields" in out
    assert "module = 'studio_customization'" in out


@pytest.mark.parametrize("yolo", [False, True])
@pytest.mark.parametrize("cli", ["claude", "gemini"])
def test_studio_views_section_absent_without_dump(cli, yolo):
    out = _render(cli, yolo=yolo, ultracode=False)
    assert STUDIO_SECTION not in out
    assert STUDIO_SETUP not in out
