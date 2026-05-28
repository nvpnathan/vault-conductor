# Vault Conductor Implementation Plan

This plan turns the Control Room Profile decisions into implementation slices. Each slice should be independently reviewable and keep Conductor's lifecycle model separate from the Obsidian plus cmux presentation layer.

## Ground Rules

- `conductor` is the normal CLI for humans, skills, and coding agents.
- `uv run conductor` is a development fallback, not the standard operating workflow.
- `conductor watch` is the only daemon. It observes, reconciles, and surfaces state; the Skill Recipe and coding agents trigger explicit work commands.
- Notifications are reserved for human attention or lifecycle milestones.
- Workspace Activity is standard vocabulary with stable labels, icons, and colors.
- Done, merge, and cleanup remain human-only.

Related decisions:

- [ADR 0001: Keep Conductor Generic And Put Local Experience In Profiles](adr/0001-control-room-profile-boundary.md)
- [ADR 0002: Automatically Create Pull Requests During Handoff](adr/0002-automatic-pr-handoff.md)
- [ADR 0003: Keep The Watch Daemon Observational](adr/0003-watch-daemon-observes-agent-triggered-actions.md)
- [ADR 0004: Hand-Maintain The Agent Control Room Skill](adr/0004-hand-maintain-agent-control-room-skill.md)

## Slice 1: Installed CLI Ergonomics

Goal: make `conductor ...` the default workflow everywhere.

Scope:

- Update `setup.sh` to run `uv tool install -e "$REPO" --force`.
- Verify `conductor --help` after installation.
- Warn when the uv tool bin directory is not on `PATH`.
- Install/update the bundled Agent Control Room skill for Codex from `skills/agent-control-room`.
- Update README examples to prefer `conductor ...`.
- Update generated dashboard notes to prefer `conductor ...`.
- Keep `uv run conductor ...` documented only for development fallback.

Acceptance:

- Fresh setup installs an executable `conductor`.
- README quick start works without prefixing every command with `uv run`.
- Dashboard notes and the Agent Control Room skill no longer teach `uv run conductor` as the normal workflow.
- Existing development workflows still work with `uv run conductor`.

Tests:

- Add or update setup/documentation tests that assert `conductor` is the preferred command text.
- Keep package script coverage for `[project.scripts] conductor`.

## Slice 2: CLI Provenance In Doctor

Goal: make installed CLI drift visible.

Scope:

- Extend `doctor_command` to report the active `conductor` executable path.
- Report package version.
- Report whether the active executable appears to be an editable install from this checkout when detectable.
- Report a warning when `conductor` is missing or does not appear to match the checkout.
- Keep `doctor --json` machine-readable.

Acceptance:

- `conductor doctor --json` includes CLI provenance fields.
- Human output clearly says whether the installed CLI is present and likely current.
- A stale or missing CLI produces a warning, not a crash.

Tests:

- Unit-test doctor output with `PATH` pointing at a fake or missing `conductor`.
- Unit-test JSON shape for provenance.

## Slice 3: Activity Vocabulary And Command

Goal: let agents report current activity through Conductor instead of raw cmux commands.

Scope:

- Add `conductor activity <TASK_ID> <activity> [--detail TEXT]`.
- Validate activity values against the standard vocabulary.
- Store the current activity in session state and task/run frontmatter only where useful.
- Render current activity through the Control Room Profile.
- Ignore or reject unknown activity labels consistently.

Initial vocabulary:

| Activity | Label | Icon | Color |
| --- | --- | --- | --- |
| `reading` | Reading | `search` | `#4c71f2` |
| `planning` | Planning | `route` | `#7c3aed` |
| `editing` | Editing | `pencil` | `#f59e0b` |
| `testing` | Testing | `flask` | `#14b8a6` |
| `debugging` | Debugging | `bug` | `#f97316` |
| `waiting` | Waiting | `clock` | `#6b7280` |
| `blocked` | Blocked | `circle-alert` | `#dc2626` |
| `reviewing` | Reviewing | `git-pull-request` | `#16a34a` |

Acceptance:

- Agents can call `conductor activity AGT-0001 testing --detail "Running pytest"`.
- Unknown activities fail with a clear message.
- `waiting` does not notify the human.
- `blocked` does not move status by itself unless paired with the explicit status/human-question workflow.

Tests:

- Unit-test accepted activity labels.
- Unit-test rejected labels.
- Unit-test session state updates.

## Slice 4: Activity Timeline Run Artifact

Goal: keep live progress visible without bloating the primary Run note.

Scope:

- Create one Activity Timeline file per Run under `30 Agent Runs`.
- Link the Activity Timeline from the Run note.
- Append meaningful activity changes with timestamp, activity, and detail.
- Avoid repeated duplicate entries for unchanged activity.
- Open the Activity Timeline in the task Workspace.

Suggested file name:

```text
30 Agent Runs/AGT-0001-RUN-001-activity.md
```

Acceptance:

- Starting a Run creates or records an Activity Timeline path.
- Activity updates append to the timeline.
- Repeated identical activity updates are deduplicated or rate-limited.
- The cmux Workspace opens the Activity Timeline as a markdown panel.

Tests:

- Unit-test timeline path creation.
- Unit-test append format.
- Unit-test duplicate suppression.
- Update start-task tests for the extra markdown panel.

## Slice 5: cmux Profile Rendering

Goal: make Obsidian plus cmux the high-visibility Control Room Profile without hardcoding lifecycle behavior into cmux calls.

Scope:

- Add profile-level rendering helpers for current Workspace Activity.
- Map current activity to `cmux set-status`.
- Add timeline entries to `cmux log` where useful.
- Use `cmux notify` only for human attention and major milestones.
- Keep notifications tied to the task Workspace.

Notification events:

- `needs-human`
- `review-diff`
- tests failed
- tests passed and ready for PR handoff
- PR opened
- workspace closed unexpectedly
- agent failed

Acceptance:

- Current activity appears as a cmux status pill with stable icon/color.
- Activity history is visible live and durable in the Activity Timeline.
- Routine reading/editing/testing activity does not create notifications.
- Human attention events create cmux notifications in the right Workspace.

Tests:

- Extend fake cmux assertions for `set-status`, `log`, and `notify`.
- Test notification routing by task Workspace.

## Slice 6: Update The Skill Recipe

Goal: make the repo skill the canonical workflow recipe for all supported coding agents.

Scope:

- Update `skills/agent-control-room/SKILL.md` to prefer `conductor ...`.
- Add the activity protocol and command recipe.
- Teach agents to call `conductor activity` before reading, planning, editing, testing, waiting, blocked, and reviewing phases.
- Teach agents to run configured tests through `conductor test`.
- Teach agents to trigger PR Handoff through the new PR flow.
- Preserve safety rules for done, merge, cleanup, secrets, and dangerous agent flags.

Acceptance:

- The skill gives agents a concrete command sequence for normal task work.
- The skill clearly distinguishes `waiting` from `blocked`.
- The skill says only humans mark `done`.
- The skill is not Codex-only; Codex is just one install surface.

Tests:

- Expand `tests/test_skill_package.py` to assert activity protocol text.
- Assert `conductor ...` is preferred over `uv run conductor ...`.
- Assert human-only `done` remains present.

## Slice 7: PR Handoff Command Flow

Goal: let agents explicitly trigger automatic PR creation after review gates.

Scope:

- Add a PR handoff path, either `conductor handoff <TASK_ID>` or `conductor pr <TASK_ID> --auto`.
- Require a non-empty diff.
- Commit cleanly when needed.
- Run only configured tests when present.
- If tests pass, create and push the PR.
- If no test command exists, create the PR but mark it as untested in the PR body and notification.
- If tests fail, do not create the PR; notify and leave the task in a review/revision state.
- Preserve human-only `done`.

Acceptance:

- Agents can trigger PR Handoff with one explicit command.
- PR body includes task, run, diff summary, test status, and untested warning when applicable.
- Failed tests stop PR creation.
- Missing tests do not block PR creation, but are visible.

Tests:

- Unit-test gate decisions with fake git and fake `gh`.
- Test no-diff rejection.
- Test missing test command path.
- Test failed test path.
- Test successful PR path moves task to `pr-opened`.

## Slice 8: Open PR In Same cmux Workspace

Goal: present the PR where the human already has task context.

Scope:

- After PR creation, open the PR URL in a browser split in the same Workspace.
- Prefer cmux browser commands over system browser commands.
- Focus the task Workspace when PR Handoff completes.
- Store enough surface metadata to reopen or refresh the PR surface later.

Acceptance:

- PR opens in the existing task Workspace.
- The browser appears as a split, not a separate unrelated workspace.
- Task note records the PR URL.
- cmux notification points to the same Workspace.

Tests:

- Extend fake cmux to assert browser open/open-split calls.
- Test behavior when no live Workspace exists: record PR URL and notify without crashing.

## Slice 9: Watch Daemon Reconciliation

Goal: keep the always-on daemon predictable while still useful.

Scope:

- Keep watch responsible for observing sessions, closed workspaces, status drift, and cmux notifications.
- Reconcile activity/status state when Workspaces disappear.
- Do not make watch silently run tests or create PRs.
- Log enough detail for operators to understand what watch did.

Acceptance:

- Closing a live Workspace still moves a running Task to a Human Gate.
- Watch does not trigger PR Handoff by itself.
- Watch can reflect agent-emitted statuses and activities into durable state.
- Watch logs are useful without being noisy by default.

Tests:

- Extend existing watch tests for activity/session state.
- Test that watch does not run tests or PR commands automatically.

## Suggested Order

1. Installed CLI ergonomics.
2. Skill Recipe update for `conductor ...` command shape.
3. Activity command and vocabulary.
4. Activity Timeline artifact.
5. cmux rendering.
6. PR Handoff command flow.
7. PR browser split.
8. Doctor provenance.
9. Watch reconciliation refinements.

This order makes the agent-facing workflow usable early, then deepens visibility and handoff automation without turning the daemon into hidden workflow logic.
