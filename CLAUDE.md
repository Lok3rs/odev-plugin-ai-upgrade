# CLAUDE.md — odev-plugin-ai-upgrade

## What this is

This plugin ships the **`odev upgrade`** command: an AI-driven migrator that walks a custom Odoo module (or a whole PS custom repo) from one Odoo version to a target version. It detects source/target versions, topologically sorts modules, provisions Odoo worktrees, pulls a shared knowledge base, renders an upgrade prompt from a Jinja template, and runs an AI agent (claude/gemini/copilot/opencode) inside the odev sandbox, then loops on `odev test --ai` to verify. This file governs Claude when editing **this plugin's own code** (`commands/`, `common/`, `config.py`, the `.j2` template). It is the contributor guide — not the upgrade domain playbook (see Extensions).

## Golden rules (non-negotiable)

1. **Lint gate before every commit.** `pre-commit run --all-files` must pass — never commit with failing hooks. Bare `flake8`/`mypy`/`pylint` miss checks (plugin deps are pinned as pre-commit `additional_dependencies`); prefer `pre-commit run`.
2. **Sibling repos are READ-ONLY reference.** `../odev` (core), `../odev-plugin-ai`, `../odev-plugin-project` may be read to learn APIs but **never modified**. All changes stay inside this plugin.
3. **Obey the odev CLI rules** (cheatsheet below) for any `odev` invocation.
4. **`templates/upgrade_prompt.md.j2` is the authoritative source of truth for upgrade behavior.** Any change to what the AI does during a migration starts there — treat it as a first-class artifact and keep code in sync with it.

## Architecture

File map (all paths relative to repo root):

- `commands/upgrade.py` → `UpgradeCommand` (`_name = "upgrade"`). The command. Subclasses `DatabaseCommand` + `ListLocalDatabasesMixin` (core) + `AICommandMixin` (odev-plugin-ai).
- `common/knowledge.py` → `KnowledgeIndex` — the migration knowledge base (clone/pull, version-pair logic, stub generation, prompt aggregation, PR open).
- `config.py` → `KnowledgeSection(Section)` (`_name = "knowledge"`), exposes `repo_url` get/set; `None` when unset → triggers first-run wizard.
- `templates/upgrade_prompt.md.j2` → the rendered AI prompt (authoritative; see "Editing the upgrade prompt").
- `__manifest__.py` → `__version__ = "1.2.0"`, `depends = ["odoo-odev/odev-plugin-ai", "odoo-odev/odev-plugin-project"]`.

### Orchestration pipeline (`_run_upgrade`, upgrade.py)

1. Resolve `repo_path = Path(args.path).resolve()`.
2. Unless `--no-ruff`: `_check_ruff_cleanliness()` — `ruff check <path> --diff --exit-zero`; warns / may flip `no_ruff=True` (only if the user declines the confirm) when the diff > 50 lines.
3. `_check_git_safety()` — on a protected branch, require `--to` and prompt to create/checkout a `<target_ver>-upgrade` feature branch (an xgram suffix is appended when available).
4. `_prepare_upgrade()` builds the context (returns early if falsy):
   - `_detect_versions()` — from `--from`/manifest/addons/db; target from `--to` (raises if missing).
   - `_get_sandbox_config()` — `target_db = "<base>_<target_ver>_upgrade"`, sandbox + extra bind dirs.
   - `_get_modules_info()` → `_get_sorted_modules()` (networkx topological sort, cycle fallback), filtered to `module_name`.
   - `_setup_upgrade_instructions()` — clone/pull `ODOO_UPGRADE_REPOSITORY`; migration-scripts text.
   - `_setup_knowledge_index_context()` — build `KnowledgeIndex`, `ensure_setup()`/`clone_or_pull()`/`_resolve_standard_deps()`.
   - `_prepare_odoo_environment([from_ver, target_ver])` — provision Odoo versions.
   - `_build_final_prompt()` — render the `.j2` template.
5. `agent = self.get_ai_agent()`.
6. `_cleanup_wizard(stage="pre-flight")` — prompt to drop leftover `*_upgrade` dbs.
7. Warn on missing `odoo_upgrade_utils` / `custom_util` skills (`_get_loaded_skills()`).
8. `agent.run(prompt, sandbox_dirs, …, database=target_db, version=target_ver, resume=args.resume)`.
9. `_verification_loop()` — loop prompting `odev test --ai <db> -V <ver> -i <modules>`, threading `--resume` session id.
10. `_sync_knowledge()` — if knowledge configured + confirmed, `ki.commit_and_pr()` opens a knowledge PR.
11. `run()` wraps the above; `finally` always calls `_cleanup_wizard(stage="post-flight")`.

### `KnowledgeIndex` public surface (`common/knowledge.py`)

`local_path` (prop), `is_configured`, `ensure_setup`, `clone_or_pull`, `get_version_pairs` (static), `get_missing_entries`, `create_stub_entries`, `load_knowledge`, `commit_and_pr`. Knowledge files live per-module as `<module>/<from>_to_<to>.md`; `index.json` is regenerated on write. Note: `_setup_knowledge_index_context` currently wires only `ensure_setup`/`clone_or_pull`/`_resolve_standard_deps` — `get_missing_entries`/`create_stub_entries`/`load_knowledge` exist but are **not yet** called from the command path.

## How it runs

Enable (resolves both declared deps automatically — manifest depends on `odev-plugin-ai` **and** `odev-plugin-project`):

```bash
odev plugin --enable odoo-odev/odev-plugin-ai-upgrade
```

Real invocations:

```bash
odev upgrade my_module --to 18.0 --task-id 12345
odev upgrade --path /path/to/ps-acme-custom --to 17.0 --from 16.0 --task-id 67890
odev upgrade sale_custom --to 18.0 --task-id 4242 --submodules
odev upgrade --to 17.0 --task-id 9999 --no-ruff
odev upgrade account_extension --to 18.0 --from 16.0 --task-id 555 --path ./addons --submodules --no-ruff
```

CLI args (declared on `UpgradeCommand`): `module_name` (positional, optional), `--path`, `--to`, `--from`, `-c/--comment`, `--task-id` (**required**), `--submodules`, `--no-ruff`, `--ultracode`, `--effort`, `--studio-views`. `cli`/`model`/`yolo`/`headless`/`resume`/`dirs` come from `AICommandMixin`.

`--ultracode` (opt-in, default off) injects the literal `ultracode` keyword + an orchestration directive at the top of the rendered prompt so the launched AI CLI fans out multi-agent workflows for substantial steps. ultracode mode is triggered purely by the prompt keyword (a real Claude Code trigger). `_run_upgrade` warns when `--ultracode` is set without `--yolo` (the restricted `--allowedTools` set can otherwise stall workflow sub-agents).

`--studio-views` (opt-in, default off) extracts ONLY the views-related tables (`STUDIO_VIEWS_TABLES` in `commands/upgrade.py`: `ir_ui_view`, `ir_model_data`, `ir_model`, `ir_model_fields` — view archs + metadata, no transactional customer data) from the source database via `LocalDatabase.dump(tables=...)` into `dumps_path/studio-views/<db>/` and bind-mounts that directory into the sandbox. On website-enabled source databases, website page views (customer content) are excluded via `dump(where={"ir_ui_view": WEBSITE_VIEWS_FILTER})` row filtering. The rendered prompt gains a "Step 2b: Studio Views Migration" section (load the dump into a scratch DB in the ephemeral cluster, migrate the studio view archs, validate them through a rolled-back gate against the fresh target-version DB, and emit artifacts: per-view XML plus a migration preferring `odoo_upgrade_utils`/`custom_util` helpers with plain-SQL fallback); under `--yolo` Step 2b delegates to the `odoo-upg-studio-views` specialist (`agents/odoo-upg-studio-views.md`). `_extract_studio_views` warns + confirms when the source DB is not neutralized; `_run_upgrade` additionally refuses/offers to drop a pre-existing host DB named like the target (it would be cloned wholesale into the sandbox, bypassing the extraction).

`--effort` (`low`/`medium`/`high`/`xhigh`/`keep`, default `high`, **claude only**) controls the launched agent's reasoning effort. The sibling's `claude.py:get_command` never passes `--effort` and `agent.run()` exposes no effort passthrough, so the launched claude otherwise inherits the user's global `~/.claude/settings.json` `effortLevel` — which for users who keep it `low` yields a low-effort migration agent. **The lever is `~/.claude/settings.json` itself**: the sandbox mounts the user's real `~/.claude` (persistent, RW), so `common/effort.py::apply_effort_override` temporarily sets `effortLevel` on the host **before** `agent.run` and `restore_effort_override` puts the original back in a `finally` (survives Ctrl-C; SIGKILL is the only gap). `effortLevel` accepts up to `xhigh` (`max`/`ultracode` are session-only and cannot be persisted); `--ultracode` bumps the applied level to `xhigh`. `--effort keep` leaves the global setting untouched. (This corrects the earlier note that effort "cannot be set from here" — it can, via the mounted settings.json, just not via a CLI flag.)

Plugin loading: `odev plugin --enable …` → `Odev.install_plugin()` clones the repo, recursively installs `depends`, symlinks into `plugins_path` (dashes→underscores), then `load_plugins()` imports it as `odev.plugins.odev_plugin_ai_upgrade`; `_register_plugin_commands()` registers `UpgradeCommand` under `_name="upgrade"`.

Integration points (from `AICommandMixin`, `../odev-plugin-ai/common/`):

- `get_ai_agent()` — returns a configured `AgentCLI` (picks CLI + model, verifies sandbox + install).
- `agent.run(prompt, sandbox_dirs, extra_bind_dirs=…, database=…, version=…, resume=…)` — runs the CLI in the sandbox with an ephemeral postgres + bound dirs (`agent.py:145`).
- `agent.get_latest_session_id()` — most recent session id for resume (`agent.py:235`).
- `_prepare_odoo_environment(versions)` — ensures Odoo worktrees exist / are current.
- `_get_loaded_skills()` — names of globally loaded `npx skills`.

## Dev workflow & tooling

Python **3.10** (`.pre-commit-config.yaml` `default_language_version`). Lint config lives in `.pre-commit-config.yaml`, `.flake8`, `.pylintrc`, `.isort.cfg`, `.prettierrc.yaml`, `.coveragerc`. No `Makefile`, no `pyproject.toml`, no CI workflow in this plugin.

```bash
pre-commit install                       # one-time: install the git hook
pre-commit run --all-files               # canonical full lint+format gate
pre-commit run <hook-id> --all-files     # one hook: black|isort|flake8|mypy|pylint|prettier|autoflake|pyupgrade|codespell
black --line-length=120 .                # format (matches black hook; excludes static/util.py)
isort --settings=. .                     # imports (black profile, 120, ODEV/ODEV_PLUGINS sections)
pylint --rcfile=.pylintrc commands common config.py
```

The full stack: prettier+plugin-xml, black (120), isort, autoflake, flake8 (max-complexity 16, B9/B950), mypy (v1.5.1), pyupgrade, pylint, safety, codespell. Per-file flake8 ignores: `commands/upgrade.py:B950`, `__init__.py:F401`.

> **ruff is not part of this plugin's lint gate.** No ruff config exists here. The only ruff usage is the *feature*: the `.j2` template and `_check_ruff_cleanliness()` run `ruff check` against the **user's migrated module**, never against this plugin's source. (The parent `../odev` repo does use ruff on itself — do not conflate.)

## Verification

Two layers:

**(a) Unit tests — TO ADD under `tests/`.** No `tests/` dir exists yet (matches the AI-plugin family: lint-gated, untested). Cover the pure logic first:
- `KnowledgeIndex.get_version_pairs` — consecutive `(from, to)` pair generation (major-only fallback + per-module pairs from migration scripts).
- `_get_sorted_modules` / topological sort — dependency ordering + cycle fallback.
- `KnowledgeIndex.load_knowledge` + `index.json` parse/regenerate (`get_missing_entries`, `create_stub_entries`).

When adding tests, mirror parent `../odev` conventions: top-level `tests/` package, `tests/tests/` grouped by area (`common/`, `commands/`), `tests/fixtures/` (`case.py` base `TestCase`, `matchers.py`, mocks), `tests/resources/`. Naming `test_<thing>.py` (the `name-tests-test` hook enforces `--pytest-test-first`). Add a `pytest.ini` (`testpaths=tests`) and `requirements-dev.txt` (`pytest`, `pytest-cov`, `coverage`, `testfixtures`) like odev's. Note: the current `.coveragerc` has only a `[report]` section — add `[run] source = …` before `coverage run -m pytest` records anything. Canonical run (odev): `coverage run -m pytest` then `coverage report`.

**(b) odev runtime — end-to-end command behavior.** Verify the live command with `odev test --ai … -i <modules>` (what `_verification_loop` drives) and `odev run --stop-after-init` against a created upgrade db. Follow the odev CLI rules below.

## odev CLI cheatsheet (mandatory)

1. **Versioning:** `-V <version>` is **required** for `odev create`; optional for `run`/`shell`/`test` when the db exists (version inferred), required when targeting a different version or the db is absent.
2. **Argument order:** odev flags (`-V`, `-f`, `-c`, `-w`, `--venv`) come **before** the database name.
3. **Create/overwrite:** always pass `-f` when a db might already exist.
4. **Log level:** use `--log-level=warn` to keep logs concise (sets odev + Odoo).
5. **Glob quoting:** always wrap `--glob` patterns in double quotes.
6. **Stop-after-init:** always include `--stop-after-init` for `odev run`/`odev deploy` verification (else the shell hangs).
7. **Addons paths:** never hand-specify standard odoo/enterprise paths — odev derives them from `-V`.
8. **`odev test`:** use specific `-t/--tags` (or `-i <modules>`); never run full suites in-session.
9. **Manifest on upgrade:** reset `__manifest__.py` version to `<odoo_major>.0.1.0.0` (e.g. `19.0.1.0.0`).

## Commit / house style

Observed convention (house style, **not** a blocking gate): Odoo `[TAG]` commits — `[REF]` / `[IMP]` / `[FIX]` / `[UPG]` / `refactor`. The upgrade flow itself emits `[UPG][task_id] module: description` with the Odoo SHA only in the commit body `Source:` line. Keep commits atomic and scoped to this plugin. The lint gate (Golden rule 1) **is** blocking; commit style is not.

## Editing the upgrade prompt

Behavior changes to the migration flow start in **`templates/upgrade_prompt.md.j2`** — it is authoritative. Rendered by `_build_final_prompt()` (`upgrade.py`) via a plain `jinja2.Template(...)` (no `Environment`/`FileSystemLoader`, no autoescape) at `templates/upgrade_prompt.md.j2`.

Available template variables: `task_id`, `from_ver`, `target_ver`, `from_odoo_path`, `target_odoo_path`, `project_path`, `k_path` (defaults to `/knowledge`), `upgrade_instructions`, `modules` (list of dicts with `.name`/`.path`), `comment`, `submodules`, `no_ruff`, `ultracode` (gates the top-of-prompt `ultracode` keyword + orchestration directive), `studio_views_dump` (in-sandbox path of the views-only SQL dump, or `None` — gates the Setup note and Step 2b), `studio_views_tables` (list of table names in that dump), `id_tag` (derived in-template as `[<task_id>]`). Note: `is_ps_custom` is passed by `_build_final_prompt()` but is **not** referenced in the template. When you change rendered behavior, update both the template and any code that supplies these variables.

## Extensions (added later)

Reserved slots — **do not write these now**, just placeholders for deeper docs layered on top of this base:

- **Upgrade domain playbook** (primary): how the team runs real migrations — Odoo SHA hunting for source provenance, knowledge-base authoring/curation workflow, per-module migration workflow, handling PS custom repos vs single modules.
- **KnowledgeIndex wiring** — documenting `get_missing_entries`/`create_stub_entries`/`load_knowledge` once they are wired into `_prepare_upgrade`.
- **Sandbox & agent internals** — deeper notes on `AgentCLI.run`, sandbox bind dirs, and session/resume mechanics.
