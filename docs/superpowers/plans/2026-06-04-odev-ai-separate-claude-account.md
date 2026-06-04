# `odev ai` Separate Claude Account (Config-Dir Override) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `odev ai`/`odev upgrade` authenticate the spawned `claude` CLI against a *separate* Claude subscription (its own `CLAUDE_CONFIG_DIR` + namespaced Keychain entry), so its token usage bills to a different account than the user's day-to-day Claude Code — opt-in, default behavior byte-for-byte unchanged.

**Architecture:** Introduce a dependency-free `config_dir.py` module in `odev-plugin-ai` that resolves the Claude config-dir name (env var > odev config setting > `.claude` default) and derives all dependent paths. `ClaudeHandler` becomes a thin delegator to it. `AgentCLI` injects `CLAUDE_CONFIG_DIR` into the sandbox env (via a new generic handler hook) and mounts the override dir instead of `~/.claude`, only when the override is active.

**Tech Stack:** Python 3.10+, odev plugin framework (`Section` config, `BaseAgentHandler`), pytest (`--import-mode=importlib`), macOS Seatbelt / Linux bwrap sandbox, `claude` CLI v2.1.162.

---

## ⚠️ Read before starting

**This plan edits the `odev-plugin-ai` repo — a sibling that is READ-ONLY for our team's day-to-day work (golden rule #2 of `odev-plugin-ai-upgrade/CLAUDE.md`).** These edits are an *intentional upstream contribution*: do them on a dedicated feature branch of `odev-plugin-ai` (its own git repo, currently on branch `macos`) and submit them as a PR to that plugin's maintainers. Do **not** treat this as a local hack to a dependency. If contributing to `odev-plugin-ai` is not sanctioned, stop and hand the design (`docs/superpowers/specs/2026-06-04-odev-ai-separate-claude-account-design.md`) to its owners instead.

**Empirical facts already verified (do not re-derive — they shape the design):**
- `claude` is **v2.1.162** → setting `CLAUDE_CONFIG_DIR` uses a *namespaced* macOS Keychain entry (`Claude Code-credentials-<sha8>`), independent of the default account's token.
- A fresh `CLAUDE_CONFIG_DIR` prints `Not logged in · Please run /login` → confirms credential isolation.
- With `CLAUDE_CONFIG_DIR=$DIR` set, `claude` keeps its global config at **`$DIR/.claude.json`** plus `$DIR/{projects,sessions,backups}/` — i.e. *inside* the dir. **Without** the env var (default), the global config is at **`~/.claude.json`** (home root, a sibling of `~/.claude`). This asymmetry is the source of the conditional logic in Task 2 and Task 4.
- The sandbox env is a strict allowlist (`agent.py::_build_env`) materialized identically by both `seatbelt.py:338-340` and `bwrap.py:270-273` → adding a key via a handler hook reaches both platforms.
- Removing the hardcoded `~/.claude` mount at `agent.py:63` is safe: every CLI's handler registers its own config dirs (gemini→`.gemini`, copilot→`.copilot`, opencode inherits claude), and `.claude` is currently registered twice for Claude.

**Repo paths (absolute):**
- Upstream change target: `/Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai`
- This plan + spec live in: `/Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai-upgrade/docs/superpowers/`

---

## File Structure

**In `odev-plugin-ai` (the PR):**
- **Create** `common/handlers/config_dir.py` — pure, stdlib-only resolution + path derivation. The single source of truth. Unit-tested.
- **Modify** `config.py` — add `claude_config_dir` property to `AiSection`.
- **Modify** `common/handlers/base.py` — add `get_extra_env()` hook (default `{}`).
- **Modify** `common/handlers/claude.py` — delegate all `.claude`-related methods to `config_dir.py`; gate the override on `cli == "claude"`; fix `inject_trust` to use the resolved global-config path.
- **Modify** `common/agent.py` — drop the hardcoded `.claude` mount (`:63`); merge `handler.get_extra_env()` into `_build_env`; route `get_latest_session_id` through the handler.
- **Create** `tests/pytest.ini` + `tests/tests/common/test_config_dir.py` — mirror the upgrade plugin's importlib-by-path pattern.
- **Modify** `README` (or equivalent docs) — document the new setting + one-time `/login`.

**In `odev-plugin-ai-upgrade` (optional follow-up, separate commit/repo — Appendix):**
- **Modify** `common/effort.py` — retarget the effort-override `settings.json` write to the resolved config dir.

---

## Task 1: Feature branch + test scaffolding in `odev-plugin-ai`

**Files:**
- Branch: in `/Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai`
- Create: `odev-plugin-ai/tests/pytest.ini`
- Create: `odev-plugin-ai/tests/tests/common/test_config_dir.py` (empty placeholder added in Task 2)

- [ ] **Step 1: Create the feature branch**

Run:
```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai
git status
git checkout -b feat-claude-config-dir-override
```
Expected: clean tree, now on `feat-claude-config-dir-override` (branched off `macos`).

- [ ] **Step 2: Create the pytest config**

Create `odev-plugin-ai/tests/pytest.ini` (mirrors `odev-plugin-ai-upgrade/tests/pytest.ini`; keeping the config under `tests/` avoids pytest treating the plugin-root `__init__.py` as a collected package):
```ini
; Config lives here (under tests/) on purpose: the plugin root holds an
; __init__.py (required so odev can import the plugin), which pytest would
; otherwise treat as a Package and try to import during collection. Keeping the
; rootdir at tests/ (no __init__.py) avoids that. Run with `pytest tests/` (or
; `python -m pytest tests/`) from the plugin root, or `pytest` from here.
[pytest]
testpaths = tests
addopts = --import-mode=importlib
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

- [ ] **Step 3: Create the test directory tree**

Run:
```bash
mkdir -p /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai/tests/tests/common
```
Expected: directory exists. (No `__init__.py` files — importlib mode + by-path loading do not need them.)

- [ ] **Step 4: Commit the scaffolding**

```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai
git add tests/pytest.ini
git commit -m "[REF] tests: add pytest scaffolding for plugin-ai"
```

---

## Task 2: The pure `config_dir.py` resolution module (TDD)

This module holds **all** the logic. It imports only the stdlib, so the test loads it by file path (no `odev` import, no relative imports) — the proven pattern from `odev-plugin-ai-upgrade/tests/tests/common/test_agents.py`.

**Files:**
- Test: `odev-plugin-ai/tests/tests/common/test_config_dir.py`
- Create: `odev-plugin-ai/common/handlers/config_dir.py`

- [ ] **Step 1: Write the failing tests**

Create `odev-plugin-ai/tests/tests/common/test_config_dir.py`:
```python
"""Tests for ``common/handlers/config_dir.py`` Claude config-dir resolution.

Loads the module by file path (importlib) so the test runs without importing the
plugin package (whose ``__init__.py`` pulls in ``odev``). The module under test
imports only the stdlib, so this works cleanly.
"""

import importlib.util
from pathlib import Path


def _plugin_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "__manifest__.py").exists():
            return parent
    raise RuntimeError("Could not locate the plugin root (no __manifest__.py found).")


def _load_config_dir_module():
    path = _plugin_root() / "common" / "handlers" / "config_dir.py"
    spec = importlib.util.spec_from_file_location("claude_config_dir_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


cfg = _load_config_dir_module()


def test_resolve_defaults_to_dot_claude(monkeypatch):
    monkeypatch.delenv("ODEV_CLAUDE_CONFIG_DIR", raising=False)
    assert cfg.resolve_claude_config_dir("") == ".claude"
    assert cfg.resolve_claude_config_dir(None) == ".claude"
    assert cfg.resolve_claude_config_dir("   ") == ".claude"


def test_resolve_uses_configured_when_env_unset(monkeypatch):
    monkeypatch.delenv("ODEV_CLAUDE_CONFIG_DIR", raising=False)
    assert cfg.resolve_claude_config_dir(".claude-odev") == ".claude-odev"


def test_resolve_env_wins_over_configured(monkeypatch):
    monkeypatch.setenv("ODEV_CLAUDE_CONFIG_DIR", ".claude-env")
    assert cfg.resolve_claude_config_dir(".claude-odev") == ".claude-env"


def test_resolve_blank_env_is_ignored(monkeypatch):
    monkeypatch.setenv("ODEV_CLAUDE_CONFIG_DIR", "   ")
    assert cfg.resolve_claude_config_dir(".claude-odev") == ".claude-odev"


def test_is_override():
    assert cfg.is_override(".claude") is False
    assert cfg.is_override(".claude-odev") is True


def test_extra_env_empty_for_default():
    assert cfg.claude_extra_env(Path("/home/u"), ".claude") == {}


def test_extra_env_absolute_path_for_override():
    assert cfg.claude_extra_env(Path("/home/u"), ".claude-odev") == {
        "CLAUDE_CONFIG_DIR": "/home/u/.claude-odev"
    }


def test_global_config_name_default_is_home_root():
    assert cfg.global_config_name(".claude") == ".claude.json"


def test_global_config_name_override_is_inside_dir():
    assert cfg.global_config_name(".claude-odev") == ".claude-odev/.claude.json"


def test_global_config_path():
    assert cfg.global_config_path(Path("/home/u"), ".claude") == Path("/home/u/.claude.json")
    assert cfg.global_config_path(Path("/home/u"), ".claude-odev") == Path(
        "/home/u/.claude-odev/.claude.json"
    )


def test_config_files_default_mounts_claude_json():
    assert cfg.config_files(".claude") == [".claude.json"]


def test_config_files_override_is_empty():
    assert cfg.config_files(".claude-odev") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai && python -m pytest tests/tests/common/test_config_dir.py -v
```
Expected: collection/import error — `config_dir.py` does not exist yet (`RuntimeError`/`FileNotFoundError` when loading the module, or all tests error).

- [ ] **Step 3: Implement `config_dir.py`**

Create `odev-plugin-ai/common/handlers/config_dir.py`:
```python
"""Resolution of the Claude config-directory override (separate-account support).

Kept dependency-free (stdlib only — no ``odev`` imports, no relative imports) so
the precedence + path-derivation logic is unit-testable in isolation by loading
this file directly via ``importlib``.

Layout asymmetry handled here (verified against ``claude`` v2.1.162):
  * Default (no override): the global config is ``~/.claude.json`` (HOME root).
  * Override (CLAUDE_CONFIG_DIR set): the global config is ``$DIR/.claude.json``
    (inside the dir), alongside ``$DIR/{projects,sessions,backups}/``.
"""

import os
from pathlib import Path

#: Env var that overrides the configured value for a single invocation.
ENV_VAR = "ODEV_CLAUDE_CONFIG_DIR"
#: Default Claude config directory name (relative to $HOME).
DEFAULT_CONFIG_DIR = ".claude"
#: Claude's global per-user config/state file name.
GLOBAL_CONFIG_FILE = ".claude.json"


def resolve_claude_config_dir(configured):
    """Return the Claude config dir NAME (relative to $HOME).

    Precedence: ``$ODEV_CLAUDE_CONFIG_DIR`` env var > ``configured`` (odev config
    value) > ``.claude``. Empty/whitespace values are treated as unset.
    """
    env_val = (os.environ.get(ENV_VAR) or "").strip()
    if env_val:
        return env_val
    configured = (configured or "").strip()
    return configured or DEFAULT_CONFIG_DIR


def is_override(config_dir):
    """True when ``config_dir`` is a non-default (separate-account) directory."""
    return config_dir != DEFAULT_CONFIG_DIR


def claude_extra_env(host_home, config_dir):
    """Extra sandbox env for the dir. ``{}`` for the default (no behavior change);
    ``{"CLAUDE_CONFIG_DIR": "<abs path>"}`` for an override so the sandboxed
    ``claude`` reads that dir and its namespaced credentials."""
    if not is_override(config_dir):
        return {}
    return {"CLAUDE_CONFIG_DIR": str(Path(host_home) / config_dir)}


def global_config_name(config_dir):
    """Relative name of the global config, accounting for the layout asymmetry.

    Default → ``.claude.json`` (HOME root). Override → ``<dir>/.claude.json``
    (inside the dir), which makes the sandbox's ``_prepare_agent_config`` treat
    it as already covered by the persistent bind-mount and skip copying the
    default account's file.
    """
    if not is_override(config_dir):
        return GLOBAL_CONFIG_FILE
    return f"{config_dir}/{GLOBAL_CONFIG_FILE}"


def global_config_path(host_home, config_dir):
    """Absolute path to the global config file for ``config_dir``."""
    return Path(host_home) / global_config_name(config_dir)


def config_files(config_dir):
    """Host config files to bind-mount. Default mounts ``~/.claude.json``;
    an override returns ``[]`` because the file lives inside the bind-mounted
    config dir already (mounting the default account's file would be wrong)."""
    return [] if is_override(config_dir) else [GLOBAL_CONFIG_FILE]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai && python -m pytest tests/tests/common/test_config_dir.py -v
```
Expected: PASS — all 12 tests green.

- [ ] **Step 5: Commit**

```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai
git add common/handlers/config_dir.py tests/tests/common/test_config_dir.py
git commit -m "[ADD] handlers: pure Claude config-dir resolution + tests"
```

---

## Task 3: Add the `claude_config_dir` config setting

**Files:**
- Modify: `odev-plugin-ai/config.py` (`AiSection`)

- [ ] **Step 1: Add the property to `AiSection`**

In `odev-plugin-ai/config.py`, inside `class AiSection(Section):`, add after the `favorite_cli` setter (before `get_favorite_model`):
```python
    @property
    def claude_config_dir(self) -> str:
        """Config dir name under $HOME for the Claude account `odev ai` uses (e.g. .claude-odev). Empty = default ~/.claude."""
        return self.get("claude_config_dir", "")

    @claude_config_dir.setter
    def claude_config_dir(self, value: str):
        self.set("claude_config_dir", value)
```

> Note: the docstring is surfaced by `odev config` (it reads `getattr(section.__class__, key).__doc__`), so keep it a single useful line.

- [ ] **Step 2: Verify the setting round-trips via the CLI**

Run (the `odev config` command parses a single dotted `section.option` key arg, then an optional value):
```bash
odev config ai.claude_config_dir .claude-odev
odev config ai.claude_config_dir
```
Expected: first command sets it; second prints a table row `claude_config_dir | .claude-odev | <docstring>`.

- [ ] **Step 3: Reset it back (leave a clean default for now)**

Run:
```bash
odev config ai.claude_config_dir ""
odev config ai.claude_config_dir
```
Expected: value is now empty (resolver will treat empty as the default `.claude`).

- [ ] **Step 4: Commit**

```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai
git add config.py
git commit -m "[ADD] config: ai.claude_config_dir setting for separate Claude account"
```

---

## Task 4: Add the generic `get_extra_env` hook to the base handler

**Files:**
- Modify: `odev-plugin-ai/common/handlers/base.py`

- [ ] **Step 1: Add the default hook**

In `odev-plugin-ai/common/handlers/base.py`, inside `class BaseAgentHandler:`, add after `get_global_config_name` (before `inject_trust`):
```python
    def get_extra_env(self) -> dict:
        """Extra environment variables to inject into the sandbox for this agent.

        Default: none. Subclasses override to add CLI-specific env (e.g. Claude's
        CLAUDE_CONFIG_DIR when a separate-account override is configured).
        """
        return {}
```

- [ ] **Step 2: Sanity-check nothing else broke (import the module)**

Run:
```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai && python -c "import ast; ast.parse(open('common/handlers/base.py').read()); print('base.py parses OK')"
```
Expected: `base.py parses OK`.

- [ ] **Step 3: Commit**

```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai
git add common/handlers/base.py
git commit -m "[ADD] handlers: get_extra_env() hook on BaseAgentHandler"
```

---

## Task 5: Wire `ClaudeHandler` to `config_dir.py`

Make `ClaudeHandler` a thin delegator. The override applies **only when `self.cli == "claude"`** so `OpenCodeHandler` (which subclasses `ClaudeHandler` with `cli == "opencode-cli"`) keeps using `.claude` exactly as today.

**Files:**
- Modify: `odev-plugin-ai/common/handlers/claude.py`

- [ ] **Step 1: Update imports**

In `odev-plugin-ai/common/handlers/claude.py`, replace the top import block:
```python
import json

from odev.common.logging import logging

from .base import BaseAgentHandler
```
with:
```python
import json

from odev.common.logging import logging

from . import config_dir
from .base import BaseAgentHandler
```

- [ ] **Step 2: Add `_config_dir` and route the dir/file methods through it**

Replace the existing `get_config_dirs`, `get_persistent_dirs`, `get_config_files`, `get_global_config_name`, and `get_agent_config_rel_path` methods (lines ~12-36) with:
```python
    def _config_dir(self) -> str:
        """Resolve the Claude config dir name. The separate-account override
        applies to the 'claude' CLI only; opencode-cli (a subclass) stays on
        '.claude'."""
        if self.cli != "claude":
            return config_dir.DEFAULT_CONFIG_DIR
        try:
            configured = self.odev.config.ai.claude_config_dir
        except Exception:
            configured = ""
        return config_dir.resolve_claude_config_dir(configured)

    def get_config_dirs(self):
        return [self._config_dir(), ".config/claude"]

    def get_persistent_dirs(self):
        return [self._config_dir(), ".config/claude", ".opencode"]

    def get_config_files(self):
        return config_dir.config_files(self._config_dir())

    def get_creds_files(self):
        return [
            "claude-credentials.json",
            ".credentials.json",
            "hosts.json",
            "hosts.yml",
            "config.yml",
            "settings.json",
            "policy-limits.json",
        ]

    def get_global_config_name(self):
        return config_dir.global_config_name(self._config_dir())

    def get_agent_config_rel_path(self):
        return self._config_dir()

    def get_extra_env(self):
        return config_dir.claude_extra_env(self.host_home, self._config_dir())
```

> The `get_creds_files` body is unchanged — it is reproduced here in full because it sits between the methods being replaced; do not drop it.

- [ ] **Step 3: Fix `inject_trust` to write to the resolved global-config path**

Replace the `# .claude.json trust (project-specific)` block inside `inject_trust` (the part that currently hardcodes `self.host_home / ".claude.json"`, lines ~53-64) with one that resolves the path from the config dir. The full method becomes:
```python
    def inject_trust(self, target_dir, trusted_paths):
        super().inject_trust(target_dir, trusted_paths)
        try:
            # Official Claude Code trust
            settings_file = target_dir / "settings.json"
            settings_data = json.loads(settings_file.read_text()) if settings_file.exists() else {}
            trusted_dirs = settings_data.get("trustedDirectories", [])
            if not isinstance(trusted_dirs, list):
                trusted_dirs = []
            for path in trusted_paths:
                if path not in trusted_dirs:
                    trusted_dirs.append(path)
            settings_data["trustedDirectories"] = trusted_dirs
            settings_file.write_text(json.dumps(settings_data, indent=2))

            # .claude.json trust (project-specific). Default: ~/.claude.json;
            # override: ~/<dir>/.claude.json (the override account's own file).
            claude_json_file = config_dir.global_config_path(self.host_home, self._config_dir())
            if claude_json_file.exists():
                try:
                    claude_data = json.loads(claude_json_file.read_text())
                    projects = claude_data.setdefault("projects", {})
                    for path in trusted_paths:
                        project = projects.setdefault(path, {})
                        project["hasTrustDialogAccepted"] = True
                    claude_json_file.write_text(json.dumps(claude_data, indent=2))
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Failed to inject Claude trust: {e}")
```

> Behavior preserved for default: `global_config_path(home, ".claude")` → `~/.claude.json`, exactly today's target.

- [ ] **Step 4: Verify the module parses and imports its helper**

Run:
```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai && python -c "import ast; ast.parse(open('common/handlers/claude.py').read()); print('claude.py parses OK')"
```
Expected: `claude.py parses OK`.

- [ ] **Step 5: Re-run the config_dir unit tests (regression guard)**

Run:
```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai && python -m pytest tests/tests/common/test_config_dir.py -v
```
Expected: PASS (unchanged — the handler delegates to the tested module).

- [ ] **Step 6: Commit**

```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai
git add common/handlers/claude.py
git commit -m "[IMP] claude handler: route config dir through config_dir resolver"
```

---

## Task 6: Update `AgentCLI` (`agent.py`)

Three edits: drop the redundant/forcing hardcoded `.claude` mount, inject the handler's extra env, and route session-id lookup through the handler.

**Files:**
- Modify: `odev-plugin-ai/common/agent.py:59-66` (`_get_agent_setup`)
- Modify: `odev-plugin-ai/common/agent.py:105-118` (`_build_env`)
- Modify: `odev-plugin-ai/common/agent.py:235-253` (`get_latest_session_id`)

- [ ] **Step 1: Remove the hardcoded `.claude` from the mount list**

In `_get_agent_setup`, change the `agent_dirs` literal (lines 59-66) — delete the `host_home / ".claude",` line:
```python
        agent_dirs = [
            host_home / ".cache",
            host_home / ".local",
            host_home / ".config" / "rtk",
            host_home / ".agents",
            host_home / ".antigravity",
        ]
```
(The handler's `get_config_dirs()` — appended just below at lines 71-72 — now supplies the correct Claude dir, default or override.)

- [ ] **Step 2: Merge `get_extra_env()` into `_build_env`**

In `_build_env`, just before `return env` (currently line 118), add the merge:
```python
        if database:
            env["PGDATABASE"] = database
        env.update(self.handler.get_extra_env())
        return env
```

- [ ] **Step 3: Route `get_latest_session_id` through the handler**

Replace the body of `get_latest_session_id` (lines 235-253) with a handler-driven version. Gemini keeps `.gemini` (its handler defines `get_agent_config_rel_path` → `.gemini`); Claude/opencode use their resolved config dir; unknown CLIs (e.g. copilot, whose handler returns `None`) still yield `None`:
```python
    def get_latest_session_id(self) -> str | None:
        """Return the ID of the most recent session for this agent CLI."""
        try:
            home = Path.home()
            config_rel = self.handler.get_agent_config_rel_path()
            if not config_rel:
                return None
            sessions_file = home / config_rel / "sessions.json"

            if sessions_file.exists():
                data = json.loads(sessions_file.read_text())
                sessions = data.get("sessions", [])
                if sessions:
                    return sessions[-1].get("id")
        except Exception as e:
            logger.debug(f"Could not read latest session id for {self.cli!r}: {e}")
        return None
```

> Equivalence check: gemini→`~/.gemini/sessions.json` (was hardcoded the same); claude→`~/.claude/sessions.json` default (was the same) or `~/<override>/sessions.json` (new, correct); opencode-cli→`~/.claude/sessions.json` (handler returns `.claude` since `cli != "claude"`, same as before); copilot→`None` (handler returns `None`, same as the old `else`).

- [ ] **Step 4: Verify the module parses**

Run:
```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai && python -c "import ast; ast.parse(open('common/agent.py').read()); print('agent.py parses OK')"
```
Expected: `agent.py parses OK`.

- [ ] **Step 5: Commit**

```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai
git add common/agent.py
git commit -m "[IMP] agent: drop hardcoded .claude mount, inject handler env, route session lookup"
```

---

## Task 7: Lint gate + default-behavior regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the plugin's lint gate**

Run (config files exist in the repo: `.flake8`, `.isort.cfg`, `.pre-commit-config.yaml`):
```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai && pre-commit run --all-files
```
Expected: all hooks pass. Fix any black/isort/flake8/mypy findings and re-run until green. (If `pre-commit` is not installed for this repo, run `pre-commit install` first, or run the individual hooks the repo defines.)

- [ ] **Step 2: Confirm DEFAULT behavior is unchanged (no override set)**

Ensure no override is active:
```bash
unset ODEV_CLAUDE_CONFIG_DIR
odev config ai.claude_config_dir ""
```
Then run a trivial headless `odev ai` against your **normal** account and confirm it still works exactly as before (mounts `~/.claude`, no `CLAUDE_CONFIG_DIR` exported):
```bash
ODEV_AI_SANDBOX_DEBUG=1 odev ai "print 'default-ok' and stop" 2>&1 | grep -i "CLAUDE_CONFIG_DIR" || echo "OK: CLAUDE_CONFIG_DIR not exported in default mode"
```
Expected: `OK: CLAUDE_CONFIG_DIR not exported in default mode` (the debug launcher echoes exported env; the var must be absent in default mode) and the agent runs as your normal account.

- [ ] **Step 3: Re-run unit tests once more**

Run:
```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai && python -m pytest tests/ -v
```
Expected: PASS.

---

## Task 8: End-to-end macOS verification with a real 2nd account

This proves credential isolation and billing separation. **Manual** (needs an interactive `/login`).

**Files:** none (verification only)

- [ ] **Step 1: Log the 2nd subscription into its own dir (HOST, once)**

Use the SAME absolute path string odev will use (`$HOME/.claude-odev`) so the Keychain namespace hash matches:
```bash
CLAUDE_CONFIG_DIR="$HOME/.claude-odev" claude
# inside claude: run /login and authenticate as ACCOUNT B, then exit
```
Verify a namespaced Keychain entry now exists (distinct from the default `Claude Code-credentials`):
```bash
security dump-keychain 2>/dev/null | grep -o '"Claude Code-credentials[^"]*"' | sort -u
```
Expected: both `"Claude Code-credentials"` (account A) and `"Claude Code-credentials-<8hex>"` (account B) appear.

- [ ] **Step 2: Negative test — point odev at an EMPTY dir, expect "Not logged in"**

```bash
mkdir -p "$HOME/.claude-empty-probe"
odev config ai.claude_config_dir .claude-empty-probe
odev ai "print hello and stop" 2>&1 | head -5
```
Expected: the agent reports `Not logged in · Please run /login` — proving odev is honoring the override dir (not falling back to account A's default Keychain).

- [ ] **Step 3: Positive test — point odev at the logged-in 2nd account**

```bash
odev config ai.claude_config_dir .claude-odev
odev ai "print hello from odev ai and stop"
```
Expected: the agent runs successfully (no "Not logged in"). Token usage for this run lands on **account B**. Confirm account B's web usage dashboard reflects the spend, and that your normal Claude Code (account A) shows no new usage from this run.

- [ ] **Step 4: Confirm trust was written to the override dir (not account A's file)**

```bash
ls -la "$HOME/.claude-odev/.claude.json" "$HOME/.claude-odev/settings.json"
```
Expected: both exist; `settings.json` contains the project path under `trustedDirectories`, and `~/.claude.json` (account A) was NOT modified by the run (compare mtime before/after).

- [ ] **Step 5: Clean up the probe + leave the override set as desired**

```bash
rm -rf "$HOME/.claude-empty-probe"
# To keep odev ai on account B: leave the setting as .claude-odev.
# To revert to account A: odev config ai.claude_config_dir ""
```

---

## Task 9: Documentation + open the PR

**Files:**
- Modify: `odev-plugin-ai/README*` (or the plugin's docs entry point — locate with `ls odev-plugin-ai/README*`)

- [ ] **Step 1: Document the feature**

Add a section to the plugin README:
```markdown
### Using a separate Claude account for `odev ai`

`odev ai` runs the `claude` CLI against your normal Claude login by default. To
bill its usage to a *different* Claude subscription, give it an isolated config
directory:

1. Log the second account into its own dir (once), using an absolute path:
       CLAUDE_CONFIG_DIR="$HOME/.claude-odev" claude   # then /login as the 2nd account
2. Point odev at it:
       odev config ai.claude_config_dir .claude-odev
3. `odev ai` now uses that account; your normal Claude Code is untouched.

Per-invocation override: `ODEV_CLAUDE_CONFIG_DIR=.claude-other odev ai "..."`.
Revert to the default account: `odev config ai.claude_config_dir ""`.

Notes: requires `claude` ≥ 2.1.56 (namespaced macOS Keychain). The path string
must match between the one-time `/login` and odev. On some macOS setups a
re-`/login` may be needed after reboot (Claude Code Keychain ACL behavior).
```

- [ ] **Step 2: Commit the docs**

```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai
git add README*
git commit -m "[DOC] document ai.claude_config_dir separate-account setting"
```

- [ ] **Step 3: Push and open the PR (only when the team approves the contribution)**

```bash
cd /Users/dawidadamski/odoo/repositories/odoo-odev/odev-plugin-ai
git push -u origin feat-claude-config-dir-override
gh pr create --base macos --title "Support a separate Claude account for odev ai (CLAUDE_CONFIG_DIR override)" --body-file -
```
PR body should summarize: the opt-in config setting + env override, the default-unchanged guarantee, the layout asymmetry handling, the dropped redundant `.claude` mount, and the v2.1.56+ Keychain-namespace dependency. Link this plan and the design spec.

---

## Appendix (optional follow-up — separate repo `odev-plugin-ai-upgrade`)

`odev upgrade`'s effort override writes `~/.claude/settings.json` (`common/effort.py::apply_effort_override` / `restore_effort_override`). When a config-dir override is active, that should target the override dir's `settings.json` instead, so the second account's effort is what gets tuned. This lives in **our** repo and depends on Task 5 (the `claude_config_dir` setting) being merged upstream first. Scope it as its own plan if pursued — it is **not** required for the `odev ai` billing-separation goal.

Sketch: in `effort.py`, compute the settings path as `Path.home() / (Odev().config.ai.claude_config_dir or ".claude") / "settings.json"` (env var `ODEV_CLAUDE_CONFIG_DIR` taking precedence, mirroring `config_dir.resolve_claude_config_dir`) instead of the hardcoded `~/.claude/settings.json`, and verify the save/restore still round-trips. Add a unit test mirroring Task 2's importlib-by-path pattern if `effort.py` stays stdlib-importable.

---

## Self-Review

**Spec coverage:**
- Override knob (config + env, env precedence) → Task 3 (config) + Task 2 (`resolve_claude_config_dir` precedence) + Task 5 (`_config_dir`). ✓
- Single source of truth for the config dir → Task 2 `config_dir.py`, delegated to in Task 5. ✓
- `CLAUDE_CONFIG_DIR` threaded into sandbox env only when overridden → Task 4 (hook) + Task 5 (`get_extra_env`) + Task 6 Step 2 (merge). ✓
- Drop the redundant hardcoded `.claude` mount → Task 6 Step 1. ✓
- Trust / global-config location under override (the spec's #1 open item) → resolved empirically; Task 2 (`global_config_name`/`global_config_path`/`config_files`) + Task 5 Step 3 (`inject_trust`). ✓
- Default behavior unchanged guarantee → Task 7 Step 2. ✓
- Keychain path-string-match constraint → Task 8 Step 1. ✓
- Restart ACL risk documented → Task 9 Step 1 notes. ✓
- `odev upgrade` effort retargeting (spec constraint #6, "out of first PR") → Appendix. ✓
- Extra hardcoded sites found during research (`agent.py:242` session lookup) → Task 6 Step 3. ✓ (beyond original spec)

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command shows expected output. The Appendix is explicitly optional and gives a concrete sketch, not a placeholder task. ✓

**Type/name consistency:** `config_dir.py` public surface used identically everywhere — `resolve_claude_config_dir(configured)`, `is_override(config_dir)`, `claude_extra_env(host_home, config_dir)`, `global_config_name(config_dir)`, `global_config_path(host_home, config_dir)`, `config_files(config_dir)`, constants `ENV_VAR`/`DEFAULT_CONFIG_DIR`/`GLOBAL_CONFIG_FILE`. Handler delegates match these signatures (Task 5). New config property `claude_config_dir` matches the CLI key `ai.claude_config_dir` and the read expression `self.odev.config.ai.claude_config_dir` (Task 3 / Task 5 Step 2). ✓
