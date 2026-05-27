# vault-conductor

`vault-conductor` is the Python CLI for an Obsidian Agent Control Room. The durable source of truth is Markdown in `~/Agent Control Room`; `conductor` owns task notes, the Kanban board, worktrees, run notes, prompts, logs, and cmux live sessions.

## Quick Start

Prerequisites: Python 3.10+, `uv`, Git, cmux, Obsidian with the Kanban plugin, and optionally `codex`, `claude`, and `gh`.

```bash
git clone <vault-conductor-repo-url>
cd vault-conductor
bash setup.sh

conductor init --vault "$HOME/Agent Control Room" --repos "$HOME/repos" --no-open
conductor doctor --json
conductor scan
conductor new --repo my-repo --title "Fix failing tests" --agent codex --status ready
conductor start AGT-0001
```

Codex and Claude use cmux providers by default:

- `codex`: `cmux codex-teams`
- `claude`: `cmux claude-teams`

Custom providers can be configured in `90 System/control-room.config.yml`.

## Agent Control Room Skill

This repo includes an agent-neutral Skill Recipe for conductor-managed vaults. `bash setup.sh` installs the Codex skill copy automatically. To reinstall it manually:

```bash
cd vault-conductor
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/agent-control-room"
cp -R skills/agent-control-room "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Restart Codex after installing the skill so it is discovered in future sessions.

## Vault Layout

```text
00 Control Room/        Kanban board and dashboards
10 Projects/            Repo/project notes
20 Agent Tasks/         One Markdown note per task
30 Agent Runs/          One Markdown note per run attempt
40 Decisions/           Human decisions
50 Templates/           Generated templates
90 System/              Config, repo registry, state
```

Runtime files live under `~/.agent-control-room/`:

```text
worktrees/<repo>/<task-id>/  Agent worktrees
logs/                        Transcript and test logs
prompts/                     Prompt files and follow-ups
state/sessions.json          Live cmux session registry
```

Use `--runtime-root <path>` to isolate those runtime files for testing or alternate control rooms.

## Commands

```bash
conductor init --no-open
conductor doctor --json
conductor scan
conductor new --repo my-repo --title "Implement feature" --status ready
conductor status
conductor start AGT-0001
conductor send AGT-0001 "Please add a regression test"
conductor activity AGT-0001 testing --detail "Running pytest"
conductor log AGT-0001 --tail 100
conductor diff AGT-0001 --stat --save
conductor test AGT-0001
conductor pr AGT-0001 --auto
conductor stop AGT-0001 --park
conductor --dry-run cleanup AGT-0001 --yes
conductor sync
conductor watch
conductor dashboard
```

Only a human may mark a task done:

```bash
conductor mark AGT-0001 done --human
```

## Statuses

`conductor` preserves these status slugs and board columns:

| Status | Column |
| --- | --- |
| `backlog` | Backlog |
| `ready` | Ready |
| `running` | Running |
| `needs-human` | Needs Human |
| `review-diff` | Review Diff |
| `needs-revision` | Needs Revision |
| `pr-opened` | PR Opened |
| `done` | Done |
| `failed` | Failed / Parked |
| `parked` | Failed / Parked |

Task note frontmatter is authoritative. `conductor sync --board-wins` is available when a human intentionally wants board placement to override task note status.

## Development

```bash
uv sync --dev
uv run pytest
conductor doctor --json
```

When the installed CLI is not available during development, use `uv run conductor ...` from this checkout as a fallback.
