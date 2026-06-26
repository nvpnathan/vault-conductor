---
name: agent-control-room
description: Use when Codex needs to bootstrap, operate, repair, or use an Obsidian Agent Control Room dashboard, conductor CLI, task notes, run logs, repo registry, Kanban board, or coding-agent worktree workflow.
---

# Agent Control Room

## Operating Context

Use the local `conductor` CLI and Markdown vault as the source of truth.

Default paths:

- Implementation repo: the checked-out `vault-conductor` repository
- Obsidian vault: `~/Agent Control Room`
- Board: `~/Agent Control Room/00 Control Room/Agent Control Room.md`
- Project notes: `~/Agent Control Room/10 Projects/`
- Task notes: `~/Agent Control Room/20 Agent Tasks/`
- Run notes: `~/Agent Control Room/30 Agent Runs/`
- Config: `~/Agent Control Room/90 System/control-room.config.yml`
- Repo registry: `~/Agent Control Room/90 System/repo-registry.yml`
- Runtime root: `~/.agent-control-room/`
- Default repos root: `~/repos`

## First Checks

Before changing dashboard state, inspect current state from the `vault-conductor` checkout:

```bash
conductor doctor
conductor status
```

If command behavior is unclear, prefer the implementation repo over memory:

```bash
conductor --help
conductor <command> --help
sed -n '1,220p' README.md
```

## Bootstrap Or Repair

Use this when the vault or CLI needs setup:

```bash
uv sync --dev
conductor init --vault "$HOME/Agent Control Room" --repos "$HOME/repos" --no-open
conductor doctor --fix
```

The canonical executable is `conductor`. Use `uv run conductor ...` only as a development fallback when the installed CLI is unavailable.

## Agent Provider Names

Use `--agent codex` in conductor task metadata. That provider launches `cmux codex-teams` by default; do not start bare `codex` for managed Agent Control Room tasks.

When debugging launch behavior, check the provider config in `90 System/control-room.config.yml` and expect the default Codex command to be:

```bash
cmux codex-teams
```

## Add A Project

When the user asks to add one repo, do not run `conductor scan` unless they want every repo under `~/repos` registered. Add only that repo to `90 System/repo-registry.yml` and create one project note under `10 Projects/`.

Validate the repo first:

```bash
git -C "$HOME/repos/<repo>" rev-parse --show-toplevel
git -C "$HOME/repos/<repo>" branch --show-current
```

Project note frontmatter:

```yaml
---
type: project
repo: <repo>
repo_path: ~/repos/<repo>
default_branch: <branch>
default_agent: codex
status: active
created: <iso timestamp>
updated: <iso timestamp>
---
```

Use `conductor scan` only when the user asks to discover/register repositories broadly.

## Create And Run Tasks

Create a card and task note:

```bash
conductor new \
  --repo <repo> \
  --title "<title>" \
  --agent codex \
  --status ready \
  --priority P2 \
  --risk low \
  --goal "<goal>" \
  --acceptance "<acceptance criterion>"
```

Start a task only when the user asks to run an agent:

```bash
conductor start AGT-0001
conductor log AGT-0001 --tail 100
conductor status
```

The runner creates a worktree at `~/.agent-control-room/worktrees/<repo>/<task-id>/`, a run note, a prompt file, and a log file.

## Activity Reporting

Report meaningful current activity with the standard vocabulary so cmux can show stable labels, icons, and colors:

```bash
conductor activity AGT-0001 reading --detail "Inspecting task parser"
conductor activity AGT-0001 planning --detail "Choosing implementation slice"
conductor activity AGT-0001 editing --detail "Updating watcher"
conductor activity AGT-0001 testing --detail "Running pytest"
conductor activity AGT-0001 debugging --detail "Investigating failing test"
conductor activity AGT-0001 waiting --detail "Waiting for test command"
conductor activity AGT-0001 blocked --detail "Need human decision: <question>"
conductor activity AGT-0001 reviewing --detail "Preparing handoff"
```

Use `waiting` for non-human waits such as command output. Use `blocked` only when a human decision is needed, and pair it with exactly one specific question plus `needs-human`.

## Review And Control Flow

Use status transitions deliberately:

```bash
conductor mark AGT-0001 needs-human
conductor send AGT-0001 "Specific follow-up instruction"
conductor mark AGT-0001 needs-revision
conductor mark AGT-0001 ready
conductor diff AGT-0001 --stat --save
conductor test AGT-0001
conductor pr AGT-0001 --auto
conductor mark AGT-0001 done --human
```

Use `conductor pr <TASK_ID> --auto` only after implementation is ready for review. It creates the PR when gates pass and opens it in the task's cmux workspace. Only the human may mark `done`; never do this automatically after tests or PR creation.

When the human asks you to commit, push, hand off, or open a PR for a task that is already in review, run `conductor pr <TASK_ID> --auto`. Do not stop after a raw `git commit` or `git push`; PR handoff is what moves the task to `pr-opened`.

## Sync And Drift

If task notes and board cards drift because a human edited Markdown or dragged cards in Obsidian:

```bash
conductor sync
```

Task frontmatter is the default source of truth. Use `conductor sync --board-wins` only when the human explicitly wants board placement to override task note status.

## Safety Rules

- Do not store secrets in task notes, run notes, logs, config, or prompts.
- Do not start real Claude/Codex runs unless the user asked for an agent to run.
- Do not merge PRs or delete worktrees automatically.
- Do not use dangerous agent flags such as yolo, danger, or bypass unless the user explicitly requests them.
- Use `--dry-run` for PR or cleanup rehearsals when possible.
- For tests of runner behavior, use a fake agent in a temporary repo rather than a real project.

## Verification

After dashboard changes, run the smallest command that proves the change:

```bash
conductor doctor
conductor status
```

For implementation changes in `vault-conductor`, verify:

```bash
uv run pytest
conductor --help
```
