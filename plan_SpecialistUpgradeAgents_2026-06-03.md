# High-Fidelity Spec & Implementation Plan — Specialist Upgrade Agents

**Plugin:** `odev-plugin-ai-upgrade` (the only writable repo; `../odev`, `../odev-plugin-ai`, `../odev-plugin-project` are READ-ONLY reference)
**Date:** 2026-06-03
**Goal:** Let the AI upgrade Lead delegate well-scoped sub-tasks to four purpose-built specialist agents, to speed up migrations and raise quality (less context pollution, parallelism, format discipline).

**Decisions locked (from clarifying round):**
1. **Delivery = Hybrid.** Ship versioned `agents/*.md` in the plugin; auto-sync into `~/.claude/agents/` at launch; the `.j2` instructs the Lead to delegate to them by name.
2. **Specialists = all four:** SHA Hunter, Render-Gate Auditor, XML View Migrator, Knowledge Curator. The Lead/orchestrator (TASKS.md, sequencing, commits, Python-resolution loop) stays the main session.
3. **CLI scope = all CLIs, best-effort.** `.md` files + `subagent_type` delegation are Claude-only; for `gemini`/`copilot`/`opencode-cli` the prompt injects the same specialist *role guidance* inline as generic "delegate this sub-task" directives.
4. **Trigger = `--yolo` → `Task`, `--ultracode`(+`--yolo`) → `Workflow` fan-out, neither → single-agent (warn).**

---

## 1. Specification

### Current State

- **Authoritative behavior lives in `templates/upgrade_prompt.md.j2`** (rendered by `commands/upgrade.py::_build_final_prompt`, lines 411–446, via a bare `jinja2.Template(...)`). Template variables available today: `task_id, from_ver, target_ver, from_odoo_path, target_odoo_path, project_path, k_path, upgrade_instructions, modules, comment, submodules, no_ruff, ultracode, id_tag`. (`is_ps_custom` is passed but unused; `cli` and `yolo` are **not** passed.)
- **The launched CLI is Claude Code** (`cli == "claude"`); also supports `gemini`, `copilot`, `opencode-cli` (`AICommandMixin`, `../odev-plugin-ai/common/mixins.py:40-56`).
- **Delegation is gated by the sandbox tool allowlist, which we cannot change.** `../odev-plugin-ai/common/handlers/claude.py::get_command` (lines 79–103):
  - non-`--yolo`: `--permission-mode acceptEdits --allowedTools "Bash(rtk:*),Bash(odev:*),Bash(git:*),Bash(pre-commit:*),Read,Edit"` — **no `Task`/`Agent`/`Write`/`Workflow`** ⇒ delegation is impossible.
  - `--yolo`: `--dangerously-skip-permissions` (no `--allowedTools`) ⇒ **all tools available, delegation works.**
  - This file is in a **read-only sibling**; the `--agents` JSON-injection flag and the allowlist are therefore **off-limits** to us.
- **`~/.claude` is the *real* user dir inside the sandbox.** `../odev-plugin-ai/common/sandbox/base.py:259-267`: `.claude` is in `get_persistent_dirs()`, so `target_dir = host_home / ".claude"` (not a playground copy), and it is mounted RW (`agent.py:_get_agent_setup`, `agent_dirs`). ⇒ **`~/.claude/agents/*.md` subagents authored on the host are visible to the sandboxed Claude Code.**
- **Claude Code subagent facts (confirmed via docs):** discovery precedence is managed-settings → `--agents` flag → project `.claude/agents/` → user `~/.claude/agents/` → plugin agents. `.md` frontmatter requires only `name` (lowercase-hyphen) + `description`; optional `tools`, `model` (`inherit` default), `disallowedTools`, `effort`, `isolation`, `skills`, etc. A subagent **cannot exceed the parent's available tools** and **cannot spawn further subagents**. Invocation: `Task` tool with `subagent_type == name`. In Workflows, fan-out agents reference a custom subagent via `agent(prompt, {agentType: '<name>'})`. File-based subagents are loaded at session start (so sync must happen *before* `agent.run`).
- **Existing delegation prompt is generic and partly impossible.** `.j2` line 20 tells the Lead to "delegate … using the `invoke_agent` tool", but in non-yolo runs no such tool exists. Ultracode (lines 1–7) already tells the Lead to author multi-agent Workflows and already warns it needs `--yolo` (`upgrade.py:554-559`).
- **Natural specialization seams in the workflow:** Step 2 XML views (37–40), Step 3 Python + the "SEARCH origin" SHA hunt (49–54), Step 5a QWeb report/mail render gate (67–130), Step 6 + Step 5a.5 knowledge curation (129–138).
- **`_build_final_prompt` runs *before* the CLI is finalized.** In `_run_upgrade` (517–573), `_prepare_upgrade()` → `_build_final_prompt()` is called, and only afterward `agent = self.get_ai_agent()` (line 539). The final CLI is decided inside `get_ai_agent()` (favorite-CLI logic / interactive select, `mixins.py:107-186`); `self.args.cli` may be `None`. ⇒ to render CLI-aware directives we must resolve the agent first.
- **No `agents/` directory, no `tests/` directory, no per-agent definitions exist.** Lint gate `pre-commit run --all-files` is the only blocking gate (CLAUDE.md golden rule 1).

### Desired State

- **Four specialist subagent definition files** ship in `odev-plugin-ai-upgrade/agents/`, namespaced `odoo-upg-*`, valid Claude-Code subagent `.md` files with scoped `tools` and a precise system-prompt body:
  - `odoo-upg-sha-hunter.md` — **read-only** breaking-change archaeologist; returns *symptom → cause → Odoo SHA → fix recommendation*; never edits, never commits.
  - `odoo-upg-render-gate.md` — owns Step 5a (enumerate reports/mail templates → static field-audit vs target source → run the `_render_qweb_html` gate → adapt dead refs).
  - `odoo-upg-xml-migrator.md` — owns Step 2 (`odev upgrade-code` + view-syntax fixes + `xmllint`), parallelizable per module/file.
  - `odoo-upg-knowledge-curator.md` — owns Step 5a.5/Step 6 (author + dedup KB entries in the strict `| Title | Explanation | SHA |` format against `index.json`).
- **Host-side sync.** A new `commands/upgrade.py::_sync_specialist_agents(cli)` copies `agents/*.md` → `~/.claude/agents/` **only when `cli == "claude"`**, idempotently (overwrite plugin-owned `odoo-upg-*` files each run so they stay current; never touch non-prefixed files), **before** `agent.run`.
- **CLI-aware prompt.** `upgrade_prompt.md.j2` receives two new variables (`cli`, `yolo`) and renders a **Specialist Agents** section with a roster + when-to-delegate rules, plus per-step delegation hooks (Steps 2/3/5a/6). Branching:
  - `yolo` + `cli == "claude"` + `ultracode` → instruct fan-out **Workflows** using `agent(..., {agentType: 'odoo-upg-…'})`.
  - `yolo` + `cli == "claude"` + not `ultracode` → instruct one-shot **`Task`** delegation (`subagent_type: 'odoo-upg-…'`).
  - `yolo` + `cli != "claude"` → inject the **full specialist methodology inline** + "delegate via your CLI's sub-task mechanism; else do it yourself in a clean sub-task" (best-effort).
  - not `yolo` → today's **single-agent** flow + an explicit "delegation disabled without --yolo" note.
- **Commit-cadence integrity.** Delegation instructions force the Lead to pass `task_id={{ task_id }}` + the atomic-commit header rules into every delegated prompt. Default policy: **specialists edit + report; the Lead commits** (avoids interleaved/parallel commits and preserves the identical `[UPG][task_id]` header). Parallel Workflow edits use `isolation: 'worktree'`.
- **Resolution-order fix.** `get_ai_agent()` is hoisted above `_prepare_upgrade()` so the final `cli` flows into `_build_final_prompt`; `yolo` comes from `self.args.yolo`.
- **Warnings.** The existing ultracode-without-yolo warning is generalized: when the user expects delegation but `--yolo` is absent, log that specialists are inert.
- **Tests + tooling.** A `tests/` package (first in this plugin) covering: agent-file validity, the prompt-render matrix, and `_sync_specialist_agents`; plus `pytest.ini`, `requirements-dev.txt`, and the `.coveragerc [run]` fix (per CLAUDE.md). `pre-commit run --all-files` stays green.

---

## 2. Implementation Plan

### Phase 1: Author the four specialist agent definitions

**Context:** These `.md` files are the canonical role definitions — their body is the subagent system prompt (Claude path) and the *same* methodology text is mirrored into the non-Claude inline branch of the `.j2`. Tools must be a **subset** of what the yolo parent has, scoped to least privilege.

**Action Items:**
- [x] Create dir `agents/`.
- [x] Create `agents/odoo-upg-sha-hunter.md`. Frontmatter: `name: odoo-upg-sha-hunter`, `description:` (when to delegate — "find the Odoo commit that introduced a breaking change"), `tools: Read, Grep, Glob, Bash(git:*), Bash(grep:*), Bash(rtk:*)` (**no Edit** — read-only), `model: inherit`. Body: input = a broken symbol/field/method + target worktree path; method = `git log -S`/`git log -L`/`git grep` in `{{ target_odoo_path }}` and the Odoo `upgrade` migration-scripts repo; output contract = `symptom → cause → clickable Odoo SHA → fix recommendation (adapt, never re-add removed fields)`; **must not edit or commit**.
- [x] Create `agents/odoo-upg-render-gate.md`. `tools: Read, Edit, Grep, Glob, Bash(odev:*), Bash(grep:*), Bash(xmllint:*)`, `model: inherit`. Body = Step 5a methodology verbatim-in-spirit (discovery greps; static field-audit vs target `models/`; the `/tmp/render_gate.py` `_render_qweb_html` script run via `odev shell`; resolve dead refs by adaptation). Output = list of `(template, dead_ref, SHA, edit)` + "RENDER GATE PASSED". Commit policy: **edit + report; Lead commits** (pass `task_id`).
- [x] Create `agents/odoo-upg-xml-migrator.md`. `tools: Read, Edit, Grep, Glob, Bash(odev:*), Bash(xmllint:*)`, `model: inherit` (note: `effort: low` candidate — mechanical). Body = Step 2 methodology (`odev upgrade-code -V … --glob "<module>/**"`; manual residue: `attrs`/`states` → `invisible`/`readonly`/`required`, `tree` → `list`; `xmllint --noout`). Scope = one module (parallelizable). Commit policy: **edit + report; Lead commits**.
- [x] Create `agents/odoo-upg-knowledge-curator.md`. `tools: Read, Edit, Grep, Glob, Bash(git:*)`, `model: inherit`. Body = Step 6/5a.5 methodology (`git log --grep="\[UPG\]" --reverse` to harvest `Source:` SHAs; write `{{ k_path }}/<module>/<from>_to_<to>.md` in `| Title | Explanation | Odoo Commit Hash |`; dedup vs `index.json`). **Does not open the PR** (that stays host-side in `_sync_knowledge`). Commit policy: edit KB files + report.
- [x] Verify each file: `name` is lowercase-hyphen, `odoo-upg-` prefix, required `name`+`description` present, `tools` subset valid, body references real template vars only as literals (these `.md` files are **not** Jinja-rendered — bake no `{{ }}`; have the Lead pass concrete paths/ids in the delegation prompt instead).

**Validation:** `python -c "import yaml,glob; [yaml.safe_load(open(f).read().split('---')[1]) for f in glob.glob('agents/*.md')]"` parses all frontmatter; each `name` matches `^odoo-upg-[a-z-]+$`; manual read confirms output contracts + "no commit" rules.

---

### Phase 2: Host-side sync of agent files into `~/.claude/agents/`

**Context:** Claude Code loads file-based subagents at session start, so the copy must happen **before** `agent.run`. Only meaningful for `cli == "claude"`; other CLIs get inline guidance instead. Must never clobber a user's own (non-`odoo-upg-`) agents.

**Action Items:**
- [x] Add `commands/upgrade.py::_sync_specialist_agents(self, cli: str) -> list[Path]`:
  - if `cli != "claude"`: `logger.debug(...)`, `return []`.
  - `agents_src = Path(__file__).parent.parent / "agents"`; `dest = Path.home() / ".claude" / "agents"`; `dest.mkdir(parents=True, exist_ok=True)`.
  - for each `*.md` in `agents_src` whose stem starts with `odoo-upg-`: `shutil.copy2(src, dest / src.name)` (overwrite → always fresh from versioned source). Collect copied paths; `logger.info` the count/names.
  - Guard: only ever write/overwrite `odoo-upg-*` names; never delete or touch other files.
- [x] Call it in `_run_upgrade` after the agent is resolved and before `agent.run(...)` (see Phase 4 for ordering). Store the returned paths on `self` for optional cleanup.
- [x] **Cleanup decision (default = leave installed, re-synced each run):** because files are namespaced + overwritten every launch, they never go stale and stay reusable across runs. Provide cleanup as optional only: document manual removal, and (optional) remove them in `run()`'s `finally` if a future `config` toggle requests ephemeral agents. Do **not** delete by default.

**Validation:** Unit test (Phase 5) with `Path.home` monkeypatched to a tmp dir: asserts the four files land in `<tmp>/.claude/agents/`, are byte-identical to source, that a second call is idempotent, that a pre-existing `my-own-agent.md` is untouched, and that `cli="gemini"` yields no writes.

---

### Phase 3: Make `upgrade_prompt.md.j2` CLI- and trigger-aware

**Context:** The template is the authoritative behavior source. Add the roster + per-step delegation hooks, branching on `cli`, `yolo`, `ultracode`. Keep Claude's prompt lean (methodology lives in the subagent bodies); give non-Claude CLIs the methodology inline.

**Action Items:**
- [x] Near the top (after the `ultracode` block, before "You are an expert Odoo Upgrade Lead" or just after Setup), add `{%- set delegate = yolo -%}` and a **Specialist Agents** section rendered only `{% if delegate %}`:
  - Roster table: the four `odoo-upg-*` names + one-line scope + "delegate when …".
  - Trigger rules block:
    - `{% if cli == 'claude' %}{% if ultracode %}` → "Author a Workflow; fan out with `agent(<role-scoped prompt>, {agentType: 'odoo-upg-…', isolation: 'worktree'})`; adversarially verify SHA-hunter findings."
    - `{% else %}` (claude, not ultracode) → "Use the `Task` tool with `subagent_type: 'odoo-upg-…'` for one-shot delegation."
    - `{% endif %}{% else %}` (non-claude) → inject the **full per-specialist methodology** inline + "delegate via your CLI's sub-task mechanism; if unavailable, perform the role yourself in a clean, scoped sub-task."
  - **Mandatory delegation contract** (all branches): "Pass `task_id={{ task_id }}` and the atomic-commit header rules into every delegated prompt. Specialists EDIT and REPORT; **you (the Lead) make the commits** following the `[UPG]{{ id_tag }} <module>: …` rules. SHA Hunter is read-only."
- [x] Add `{% if not delegate %}` note: "Specialist delegation is disabled (the sandbox restricts tools without `--yolo`). Perform all steps inline." (This also replaces the now-misleading generic `invoke_agent` instruction.)
- [x] Rewrite line 20's generic Delegation bullet to point at the roster (or gate it behind `{% if not delegate %}` keep-inline / `{% else %}` use-specialists).
- [x] Add per-step delegation hooks (each `{% if delegate %}`):
  - Step 2 (XML, ~37–40): "Delegate to `odoo-upg-xml-migrator` (one per module; parallel under ultracode)."
  - Step 3 "SEARCH origin" (~50–51): "Delegate the SHA hunt to `odoo-upg-sha-hunter`; apply the fix yourself."
  - Step 5a (~67): "Delegate the entire render gate to `odoo-upg-render-gate`."
  - Step 6 / Step 5a.5 (~129–138): "Delegate KB authoring to `odoo-upg-knowledge-curator`."

**Validation:** Phase-5 render-matrix test. Manual: render with `jinja2` for `cli∈{claude,gemini}`, `yolo∈{T,F}`, `ultracode∈{T,F}` and eyeball that each branch emits the intended block and nothing leaks across branches.

---

### Phase 4: Wire `cli`/`yolo` through the command + fix resolution order

**Context:** `_build_final_prompt` needs the **final** CLI, which is only known after `get_ai_agent()`. Hoist agent resolution; thread `cli` + `yolo` into the render.

**Action Items:**
- [x] In `_run_upgrade` (`upgrade.py:517`), move `agent = self.get_ai_agent()` (currently line 539) to **before** `prepared = self._prepare_upgrade()` (line 524). Keep `_check_ruff_cleanliness`/`_check_git_safety` before it.
- [x] Thread the resolved cli: simplest path — pass `agent.cli` and `self.args.yolo` into `_prepare_upgrade(cli, yolo)` → `_build_final_prompt(..., cli, yolo)`; add `cli=cli, yolo=yolo` to the `template.render(...)` call (`upgrade.py:431-446`).
- [x] Call `self._sync_specialist_agents(agent.cli)` after resolving the agent, before `agent.run(...)` (line 561).
- [x] Generalize the warning block (`upgrade.py:554-559`): if `not self.args.yolo` and (delegation expected), `logger.warning("Specialist delegation requires --yolo; running single-agent.")`; keep the existing stronger ultracode-specific message.
- [x] Confirm no other caller of `_prepare_upgrade`/`_build_final_prompt` breaks from the new params (grep: only `_run_upgrade` calls `_prepare_upgrade`; `_build_final_prompt` only from `_prepare_upgrade`).

**Validation:** `python -c "import ast; ast.parse(open('commands/upgrade.py').read())"`; then runtime smoke (Phase 6). Confirm interactive favorite-CLI prompt now appears before env prep (acceptable UX change).

---

### Phase 5: Tests + test tooling (first `tests/` in this plugin)

**Context:** No `tests/` exists yet; follow parent `../odev` conventions (CLAUDE.md "Verification"). Cover the new pure/host logic and the prompt contract.

**Action Items:**
- [x] Scaffold `tests/`, `tests/tests/commands/`, `tests/fixtures/`; add `pytest.ini` (`testpaths=tests`), `requirements-dev.txt` (`pytest, pytest-cov, coverage, testfixtures, pyyaml, jinja2`), and add `[run] source = commands,common,config.py` to `.coveragerc` (currently `[report]`-only).
- [x] `tests/tests/test_agent_definitions.py`: every `agents/*.md` parses, `name` matches `^odoo-upg-[a-z-]+$` and equals a known set, `description` non-empty, `tools` ⊆ allowed superset, `odoo-upg-sha-hunter` has **no `Edit`**, no literal `{{` in bodies.
- [x] `tests/tests/commands/test_prompt_render.py`: render `templates/upgrade_prompt.md.j2` over the `{cli, yolo, ultracode}` matrix; assert roster iff `yolo`; `Task`/`subagent_type` iff `claude & yolo & !ultracode`; `Workflow`/`agentType` iff `claude & yolo & ultracode`; inline methodology iff `yolo & cli!=claude`; "disabled without --yolo" note iff `!yolo`; `task_id` threaded into the delegation contract.
- [x] `tests/tests/commands/test_specialist_sync.py`: monkeypatch `Path.home`; assert the four files copied for `cli="claude"`, idempotent, non-prefixed file preserved, no-op for `cli="gemini"`.
- [ ] (Recommended, pre-existing gap) stubs for `KnowledgeIndex.get_version_pairs` and `_get_sorted_modules` topo-sort — **deferred** (out of scope for this feature; pre-existing untested logic, not touched by these changes).

**Validation:** `coverage run -m pytest && coverage report` green; then `pre-commit run --all-files` green (black 120 / isort / flake8 / mypy / pylint / prettier / codespell — golden rule 1).

---

### Phase 6: End-to-end runtime validation

**Context:** Prove the four trigger paths behave per the locked decisions on a real upgrade db. Obey the odev CLI cheatsheet (`-V` before db name, `--stop-after-init`, quoted `--glob`).

> **NOT YET RUN — requires a live environment** (an installed AI CLI, provisioned Odoo worktrees, and a real custom module). These are acceptance checks to run before merge; they could not be executed in the implementation sandbox.

**Action Items:**
- [ ] `--yolo` (claude): run `odev upgrade <mod> --to <V> --from <V0> --task-id <N> --yolo`; confirm `~/.claude/agents/odoo-upg-*.md` present and the Lead uses `Task` delegation; specialists edit, Lead commits with identical `[UPG][<N>]` headers.
- [ ] `--ultracode --yolo` (claude): confirm the Lead authors a Workflow with `agentType: 'odoo-upg-…'` fan-out (parallel XML migration per module; SHA-hunter findings adversarially verified).
- [ ] **plain** (no `--yolo`): confirm single-agent flow, the "delegation disabled" prompt note, and the host warning; confirm no `Task`/delegation attempts stall.
- [ ] `--cli gemini --yolo`: confirm **no** file sync and that inline best-effort specialist guidance is present in the rendered prompt.
- [ ] Confirm SHA Hunter never edits/commits; confirm Knowledge Curator writes KB files but the PR is still opened by host-side `_sync_knowledge`.

**Validation:** Each path matches the trigger matrix; `git log` shows atomic, correctly-headed commits; `~/.claude/agents/` contains only the four `odoo-upg-*` files plus any pre-existing user agents (untouched).

---

## Black-Hat Risks & Mitigations

- **Writing to global `~/.claude/agents/` (outside the plugin).** → Namespace `odoo-upg-`, overwrite only our files, never delete/touch others; idempotent re-sync each run; gated to `cli=="claude"`.
- **Subagent `tools` cannot exceed parent; no `Task` without `--yolo`.** → Delegation is honestly gated to `--yolo`; non-yolo renders single-agent + warning. Specialist `tools` are subsets of the open yolo parent.
- **Subagents can't spawn subagents.** → Only the **Lead** authors Workflows / issues `Task`; specialist bodies state they must not delegate further.
- **`--agents` flag / allowlist are the cleaner levers but live in the read-only sibling.** → Use file-sync instead; do not modify `../odev-plugin-ai`. (Note for a *separate* cross-repo PR: adding `Task`/`Agent` to the non-yolo allowlist, or an `--agents` passthrough, would enable non-yolo delegation — out of scope here.)
- **Parallel commits race / wrong headers.** → Hard rule: specialists edit + report, **Lead commits**; parallel edits use `isolation: 'worktree'`; `task_id` + header rules threaded into every delegated prompt.
- **Methodology duplicated (`.md` bodies vs non-Claude inline `.j2` block).** → Accept minor duplication (the `.md` files can't be Jinja-rendered into `~/.claude`); `.md` files are canonical; add a follow-up note to factor shared text if it drifts.
- **Stale agent files after a plugin update.** → Overwrite-sync every launch keeps them current.
- **Token cost up (more agents).** → Already accepted under ultracode; Task path is opt-in via `--yolo`; mechanical agents can carry `effort: low`.
- **Resolution-order change surfaces the favorite-CLI prompt earlier.** → Acceptable; verified no other caller depends on the old order.

---

## Execution Status — 2026-06-03

**Phases 1–5: implemented & validated. Phase 6: pending live run.**

Files created: `agents/odoo-upg-{sha-hunter,render-gate,xml-migrator,knowledge-curator}.md`, `common/agents.py`, `tests/pytest.ini`, `tests/tests/test_agent_definitions.py`, `tests/tests/commands/test_prompt_render.py`, `tests/tests/common/test_agents.py`, `requirements-dev.txt`.
Files modified: `commands/upgrade.py` (hoisted `get_ai_agent()`, threaded `cli`/`yolo`, `_sync_specialist_agents`, generalized warning), `templates/upgrade_prompt.md.j2` (Specialist Agents section + per-step hooks + trigger branching), `.coveragerc` (`[run]` section).

**Validated locally** (odev venv python 3.13 + jinja2/pyyaml/pytest):
- 4 agent files: frontmatter parses, names match `^odoo-upg-[a-z-]+$`, sha-hunter has no `Edit`, no Jinja leak.
- Template render matrix (claude/gemini × yolo × ultracode): roster iff `--yolo`; `Task subagent_type` iff claude+yolo+!ultracode; `Workflow agentType` iff claude+yolo+ultracode; inline methodology iff yolo+non-claude; "disabled" note iff !yolo; legacy `invoke_agent` removed everywhere.
- Sync helper: writes 4 files for claude, idempotent, no-op for non-claude, never clobbers foreign agents.
- `pytest tests/` → **38 passed**. AST parse clean. black (current)/isort/file-hygiene clean.

**Deviations from plan (justified):**
1. Sync logic extracted to odev-free `common/agents.py` (method `_sync_specialist_agents` is a thin wrapper) — enables unit tests without the odev runtime.
2. Sync test at `tests/tests/common/test_agents.py` (mirrors code location) instead of `…/commands/test_specialist_sync.py`.
3. `pytest.ini` lives in `tests/` (not plugin root): the plugin-root `__init__.py` (required for odev plugin loading) makes pytest treat the root as a Package and fail to import it; rootdir must be a dir without `__init__.py`. Run with `pytest tests/` / `coverage run -m pytest tests/`.
4. Test dirs have no `__init__.py`; the sync test loads `common/agents.py` via `importlib` so the plugin's odev-importing `__init__.py` is never executed.
5. All four agents use `model: inherit`, no `effort` field (cross-version safe).

**Outstanding before merge (could not run in this environment):**
- **Full lint gate** `pre-commit run --all-files`: blocked here — `.pre-commit-config.yaml` pins `python3.10`, which is absent on this machine (pre-commit failed to build the hook env). Must be run in the dev env (golden rule). black/isort/hygiene were verified with available tooling; the only black diff is a 22.3.0↔26.x blank-line artifact that the existing committed code shares.
- **Phase 6 live acceptance** (the 5 `odev upgrade` runs above).
