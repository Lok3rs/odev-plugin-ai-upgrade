"""Tests for ``common/effort.py`` — temporary effortLevel override + restore.

Loads the module by file path (importlib) so the test runs without importing
the plugin package (whose ``__init__.py`` pulls in ``odev``).
"""

import importlib.util
import json
from pathlib import Path


def _plugin_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "__manifest__.py").exists():
            return parent
    raise RuntimeError("Could not locate the plugin root (no __manifest__.py found).")


def _load():
    path = _plugin_root() / "common" / "effort.py"
    spec = importlib.util.spec_from_file_location("upg_effort_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


effort = _load()


def _write(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def test_override_then_restore_existing_value(tmp_path):
    s = tmp_path / "settings.json"
    _write(s, {"effortLevel": "low", "theme": "dark"})

    token = effort.apply_effort_override("high", path=s)
    assert token is not None and token["had_key"] and token["old_value"] == "low"
    data = json.loads(s.read_text())
    assert data["effortLevel"] == "high"
    assert data["theme"] == "dark"  # other keys preserved

    effort.restore_effort_override(token)
    data = json.loads(s.read_text())
    assert data["effortLevel"] == "low"  # restored
    assert data["theme"] == "dark"


def test_restore_removes_key_when_absent_before(tmp_path):
    s = tmp_path / "settings.json"
    _write(s, {"theme": "dark"})  # no effortLevel

    token = effort.apply_effort_override("xhigh", path=s)
    assert token is not None and token["had_key"] is False
    assert json.loads(s.read_text())["effortLevel"] == "xhigh"

    effort.restore_effort_override(token)
    data = json.loads(s.read_text())
    assert "effortLevel" not in data  # key removed
    assert data["theme"] == "dark"


def test_noop_when_already_at_target(tmp_path):
    s = tmp_path / "settings.json"
    _write(s, {"effortLevel": "high"})
    assert effort.apply_effort_override("high", path=s) is None


def test_invalid_level_is_noop(tmp_path):
    s = tmp_path / "settings.json"
    _write(s, {"effortLevel": "low"})
    assert effort.apply_effort_override("max", path=s) is None
    assert effort.apply_effort_override("ultracode", path=s) is None
    assert json.loads(s.read_text())["effortLevel"] == "low"  # untouched


def test_preserves_other_keys_through_external_mutation(tmp_path):
    # Simulate the sandbox adding trustedDirectories between apply and restore.
    s = tmp_path / "settings.json"
    _write(s, {"effortLevel": "low"})
    token = effort.apply_effort_override("high", path=s)
    data = json.loads(s.read_text())
    data["trustedDirectories"] = ["/x"]  # external augmentation
    _write(s, data)

    effort.restore_effort_override(token)
    data = json.loads(s.read_text())
    assert data["effortLevel"] == "low"
    assert data["trustedDirectories"] == ["/x"]  # external change kept


def test_missing_file_then_restore(tmp_path):
    s = tmp_path / "nested" / "settings.json"  # does not exist yet
    token = effort.apply_effort_override("high", path=s)
    assert token is not None and token["had_file"] is False
    assert json.loads(s.read_text())["effortLevel"] == "high"
    effort.restore_effort_override(token)
    assert "effortLevel" not in json.loads(s.read_text())


def test_unreadable_settings_is_noop(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text("{not json", encoding="utf-8")
    assert effort.apply_effort_override("high", path=s) is None
    assert s.read_text() == "{not json"  # left untouched
