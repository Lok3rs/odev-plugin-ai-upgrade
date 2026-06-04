# Design: Let `odev ai` run on a separate Claude account (isolated config dir)

- **Date:** 2026-06-04
- **Status:** Design — pending implementation plan
- **Target of the code change:** `odev-plugin-ai` (sibling repo, branch `macos`) — proposed as an **upstream PR** (read-only for our team; we do not patch it locally)
- **Author repo for this spec:** `odev-plugin-ai-upgrade` (ours)

## Problem & goal

`odev ai` (and `odev upgrade`, which shares the same AI infrastructure) currently runs against **the same Claude account** as the user's day-to-day Claude Code login. The goal is to let `odev ai` authenticate as a **different Claude Pro/Max subscription** (separate email, OAuth `/login`) so that its token usage and billing are **cleanly separated** from the user's personal Claude Code. Set-once, then automatic.

This is explicitly **not** about API keys, per-account model selection, or live multi-account switching — just routing `odev ai` to a second, isolated subscription.

## How auth works today (ground truth)

`odev ai` does not talk to Anthropic directly. It **spawns the `claude` CLI** inside a sandbox (bwrap on Linux, Seatbelt/`sandbox-exec` on macOS) and lets that CLI use its own native credentials.

- Command entry: `odev-plugin-ai/commands/ai.py` (`AICommand`, `_name = "ai"`).
- Claude command built in `odev-plugin-ai/common/handlers/claude.py::get_command` → `["claude", ...]`.
- The sandbox **bind-mounts the user's real `~/.claude`** read-write so the spawned `claude` reads its credentials/sessions from there.
- On macOS the Seatbelt profile **intentionally allows Keychain access** (`odev-plugin-ai/common/sandbox/seatbelt.py:62-67`): macOS CLIs authenticate to their LLM provider via Keychain, so blocking it would break `/login`.
- On macOS, the OAuth token itself lives in the **Keychain** (item `Claude Code-credentials`), not in a file under `~/.claude`. The default account here is `dawidadamski`.

## Why a second account is impossible today — two blockers

Both live in `odev-plugin-ai`:

1. **The config dir is hardcoded.** `~/.claude` is bind-mounted via a literal `host_home / ".claude"` in `common/agent.py:63`, and the `ClaudeHandler` returns the literal `".claude"` from `get_config_dirs` (`:13`), `get_persistent_dirs` (`:16`), and `get_agent_config_rel_path` (`:36`). Note `agent.py:71-72` *also* appends the handler's `get_config_dirs()`, so `.claude` is effectively registered twice — the hardcoded entry at `:63` is redundant.
2. **The sandbox env is a strict allowlist.** `AgentCLI._build_env` (`common/agent.py:105-118`) emits only `HOME, USER, SHELL, LANG, PYTHONPATH, PATH, ODEV_*, PGDATABASE`. That dict is materialized verbatim into the launcher (`common/sandbox/seatbelt.py:338-340`; Linux equivalent `bwrap.py` `--setenv`). So even if the user exports `CLAUDE_CONFIG_DIR`, it is stripped and never reaches the sandboxed `claude`.

Net effect: the sandboxed `claude` always reads `~/.claude` + the **default** Keychain entry → always the primary account. There is no existing seam to redirect it.

## What makes the fix viable

- The installed `claude` is **v2.1.162** (≥ 2.1.56), which **namespaces the macOS Keychain entry by a SHA-256 of the `CLAUDE_CONFIG_DIR` path** (`Claude Code-credentials-<sha8>`). Two config dirs ⇒ two independent OAuth tokens, no collision.
- The Seatbelt profile already allows Keychain access, so a namespaced entry is reachable from inside the sandbox.

So the only missing pieces are: (a) let odev mount a *different* config dir, and (b) pass `CLAUDE_CONFIG_DIR` into the sandbox — **both gated on an explicit opt-in** so default behavior is unchanged.

## Design overview

Introduce a **single source of truth** for the Claude config-dir name, defaulting to `.claude`. When an override is set:

1. The `ClaudeHandler` reports the override dir (e.g. `.claude-odev`) everywhere instead of `.claude`, so the sandbox bind-mounts `~/.claude-odev` (RW, persistent) and injects trust into the right place.
2. `AgentCLI` adds `CLAUDE_CONFIG_DIR=$HOME/.claude-odev` to the sandbox env (via a new generic handler hook), so the sandboxed `claude` reads that dir and the matching namespaced Keychain entry.

When **no** override is set, no `CLAUDE_CONFIG_DIR` is emitted and the mounted dir stays `.claude` — i.e. byte-for-byte identical to today.

### The override knob — config primary, env escape hatch

- **Primary (persistent):** a new odev config setting `config.ai.claude_config_dir`, mirroring the existing `favorite_cli` pattern in `odev-plugin-ai/config.py::AiSection`. Empty string = default `.claude`.
  - Set via: `odev config ai.claude_config_dir .claude-odev` (the `odev config` CLI takes a single dotted `section.option` key arg, then the value)
- **Escape hatch (per-invocation):** environment variable `ODEV_CLAUDE_CONFIG_DIR`. If set and non-empty, it **takes precedence** over the config setting for that run.
- Resolution order, computed once in the handler: `ODEV_CLAUDE_CONFIG_DIR` (env) → `config.ai.claude_config_dir` → `".claude"` (default).
- The value is a **directory name relative to `$HOME`** (e.g. `.claude-odev`), consistent with how every other config dir is modeled (`host_home / rel_dir`).

## Detailed changes (illustrative — finalize in the implementation plan)

All edits are in `odev-plugin-ai`.

### `common/handlers/base.py`
Add a generic, default-empty env hook so a handler can contribute extra sandbox env vars:
```python
def get_extra_env(self) -> dict:
    """Extra environment variables to inject into the sandbox for this agent."""
    return {}
```

### `common/handlers/claude.py`
Add one private resolver as the single source of truth, and route everything through it:
```python
def _config_dir(self) -> str:
    env = os.environ.get("ODEV_CLAUDE_CONFIG_DIR")
    if env:
        return env
    try:
        configured = self.odev.config.ai.claude_config_dir  # AiSection property
    except Exception:
        configured = ""
    return configured or ".claude"
```
Then:
- `get_config_dirs` → `[self._config_dir(), ".config/claude"]`
- `get_persistent_dirs` → `[self._config_dir(), ".config/claude", ".opencode"]`
- `get_agent_config_rel_path` → `self._config_dir()`
- `get_extra_env` → `{} if self._config_dir() == ".claude" else {"CLAUDE_CONFIG_DIR": str(self.host_home / self._config_dir())}`
- `inject_trust` (`:54`) and `get_global_config_name` (`:33`) must derive the global-config/trust path from `_config_dir()` rather than the hardcoded `.claude.json` at home root (see Correctness constraint #2).

### `common/agent.py`
- Remove the redundant hardcoded `host_home / ".claude"` from the `agent_dirs` list (`:63`); the handler's `get_config_dirs()` (appended at `:71-72`) is now authoritative.
- Merge handler-contributed env into `_build_env` (`:105-118`):
```python
env.update(self.handler.get_extra_env())
```

### `common/config.py` (in `odev-plugin-ai`)
Add to `AiSection`, mirroring `favorite_cli`:
```python
@property
def claude_config_dir(self) -> str:
    """Config dir name (relative to $HOME) for the Claude account odev ai uses. Empty = default ~/.claude."""
    return self.get("claude_config_dir", "")

@claude_config_dir.setter
def claude_config_dir(self, value: str):
    self.set("claude_config_dir", value)
```

## One-time user setup, then automatic

```bash
# 1. Log the 2nd subscription into its own isolated config dir (on the HOST, once).
#    Use the SAME absolute path string odev will use (see Correctness constraint #1).
CLAUDE_CONFIG_DIR="$HOME/.claude-odev" claude     # then run /login as account B

# 2. Point odev ai at it (persistent):
odev config ai.claude_config_dir .claude-odev

# 3. Done. `odev ai ...` now bills account B; plain Claude Code stays on account A.
#    Per-run override, if ever needed:
ODEV_CLAUDE_CONFIG_DIR=.claude-other odev ai "..."
```

## Correctness constraints & risks (must verify during implementation)

1. **Keychain namespace = hash of the path string.** Claude derives the namespaced Keychain item from the `CLAUDE_CONFIG_DIR` *value*. The string used at host-`/login` time must match what odev injects. **Mandate:** odev always injects the absolute path `str(self.host_home / dir)`; the user logs in with the same absolute path. Verify the resolved hash/entry matches (e.g. `security find-generic-password -l "Claude Code-credentials-<sha8>"`).
2. **`.claude.json` / trust location under `CLAUDE_CONFIG_DIR`.** With the var set, Claude's global config + trust state likely live inside the override dir, not at `~/.claude.json`. `inject_trust` and `get_global_config_name` must point at the override dir so the sandboxed `claude` actually sees the trusted directories (otherwise it may re-prompt for trust or fail headless).
3. **Restart ACL bug (Claude Code #19456).** Namespaced Keychain entries may need a re-`/login` after reboot on some macOS configurations. This is a Claude Code issue, not fixable in odev; document it as a known limitation.
4. **Dropping the hardcoded `.claude` mount.** Confirm no other CLI (gemini, copilot, opencode) relied on `~/.claude` being auto-mounted via `agent.py:63`. They register their own dirs through their handlers, so removal should be safe — verify by running each CLI through the sandbox.
5. **Default-path no-op guarantee.** When the resolved dir is `.claude`, `get_extra_env()` must return `{}` (no `CLAUDE_CONFIG_DIR` emitted) so existing single-account users see zero behavior change.
6. **`odev upgrade` shares this path.** Effort-level override writes `~/.claude/settings.json` (`odev-plugin-ai-upgrade/common/effort.py`). If `odev upgrade` is run under an override dir, confirm the effort logic targets the override dir's `settings.json`, not the default. (May be out of scope for the first PR, but flag it.)

## Testing plan

- **Default unchanged:** with no override, diff the generated launcher env + bind list against current `macos` branch behavior — must be identical.
- **Override active (macOS):** set `claude_config_dir = .claude-odev`, confirm the launcher exports `CLAUDE_CONFIG_DIR=$HOME/.claude-odev`, the sandbox binds `~/.claude-odev`, and a headless `odev ai` run authenticates as account B (verify via the namespaced Keychain entry and account identity in output).
- **Env precedence:** with both config and `ODEV_CLAUDE_CONFIG_DIR` set, the env wins.
- **Trust/headless:** confirm no trust re-prompt under the override dir (constraint #2).
- **Other CLIs:** smoke-test gemini/copilot still launch after the `agent.py:63` removal (constraint #4).

## Scope

**In:** config-dir override (config setting + env escape hatch), `CLAUDE_CONFIG_DIR` env threading, trust/global-config path correctness, docs.

**Out (YAGNI):** API-key auth mode, per-account model selection, interactive multi-account switching UI, gemini/copilot config-dir overrides, and (tentatively) the `odev upgrade` effort-file retargeting (flagged in constraint #6).

## Open questions / verification items

- Exact location Claude uses for the global config / trust file when `CLAUDE_CONFIG_DIR` is set (constraint #2) — verify empirically before finalizing `inject_trust` / `get_global_config_name`.
- Whether the handler can read `self.odev.config.ai.claude_config_dir` directly, or needs a different accessor for the AiSection — confirm the config access API.
- Whether `odev upgrade`'s effort override (constraint #6) is in or out of the first PR.
