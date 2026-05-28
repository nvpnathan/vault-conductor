# Vault Conductor

Vault Conductor coordinates coding agents through an Obsidian-based control room and cmux workspaces while keeping the human in charge of final review.

## Language

**Conductor**:
The lifecycle engine for agent work. It owns tasks, runs, status transitions, review gates, and project automation without depending on one person's local workspace habits.
_Avoid_: Personal dashboard script, Obsidian plugin

**Conductor CLI**:
The installed command-line interface for operating Conductor. The Conductor CLI is the normal interface for humans, skills, and coding agents; repository-local `uv run conductor` usage is a development fallback. Doctor checks should make CLI provenance visible so humans can detect when the installed command has drifted from the intended checkout.
_Avoid_: uv run as the standard user workflow

**Setup**:
The machine-level bootstrap step for installing or updating local Conductor tooling such as the CLI, cmux hooks, and agent skills. Setup is distinct from Init, which prepares the Agent Control Room itself.
_Avoid_: Init when referring to global tool installation

**Init**:
The Agent Control Room bootstrap step for creating or repairing vault-local folders, notes, templates, configuration, and board structure. Init should not unexpectedly modify global agent or shell configuration.
_Avoid_: Setup when referring to vault-local state

**Watch Daemon**:
The always-running Conductor process that observes the Agent Control Room and cmux, reconciles live state, and surfaces attention signals. The Watch Daemon should not be the hidden author of agent work; coding agents and skills trigger explicit activity, test, and PR handoff commands.
_Avoid_: Workflow brain, autonomous agent

**Agent Control Room**:
The human-facing operating space where agent work is planned, monitored, reviewed, and reconciled. One Agent Control Room contains many **Tasks** and many **Runs**.
_Avoid_: Dashboard, vault, project board when referring to the whole operating space

**Control Room Profile**:
A named experience that connects **Conductor** to a particular human-facing environment such as Obsidian plus cmux. A profile expresses presentation and workflow preferences, including default Workspace layout, without redefining the underlying task lifecycle.
_Avoid_: Hardcoded local setup, personal integration

**Skill Recipe**:
The agent-facing workflow guidance that tells coding agents when to call Conductor commands. A Skill Recipe is canonical for the Agent Control Room workflow across supported coding agents, while each agent may have a different installation surface.
_Avoid_: Hidden daemon rule, prompt-only convention

**Task**:
A unit of requested agent work owned by a human. A Task may have many **Runs**, but only one current Run at a time.
_Avoid_: Ticket, card, job

**Run**:
One attempt by an agent to work on a Task. A Run belongs to exactly one Task.
_Avoid_: Session when referring to the attempt record

**Workspace**:
The live place where a Run is observed and controlled. A Workspace may show terminals, notes, browsers, or other panes for the same Task.
_Avoid_: Run, task note

**Workspace Activity**:
Ephemeral visibility into what a Run appears to be doing right now, such as reading, editing, testing, or waiting. Workspace Activity uses a standard vocabulary with profile-defined labels, icons, and colors plus optional free-text detail; unknown activity labels are not part of the domain language.
_Avoid_: Notification, status

**Activity Timeline**:
A chronological view of meaningful Workspace Activity for one Run. The current Workspace Activity answers what is happening now; the Activity Timeline answers what happened since the human last looked, and it is preserved as a separate Run artifact in the Agent Control Room.
_Avoid_: Task log when referring only to live activity history

**Blocked Activity**:
Workspace Activity showing that a Run is currently stuck or waiting. Blocked Activity becomes a Human Gate only when paired with a specific human question.
_Avoid_: needs-human

**Waiting Activity**:
Workspace Activity showing that a Run is waiting on non-human work such as command output, network response, or tool execution. Waiting Activity does not notify the human by itself.
_Avoid_: Blocked Activity

**Notification**:
A human attention signal emitted by the control room experience for required action or a major lifecycle milestone. A Notification is not itself a Task status and should not be used for routine Workspace Activity.
_Avoid_: Status, log entry

**Human Gate**:
A point in the lifecycle where the human must decide before Conductor continues. Done is always behind a Human Gate.
_Avoid_: Approval as a synonym for every status change

**Review**:
The human evaluation of agent output before completion. Review can include reading the diff, checking test results, and deciding whether a Task needs revision or can advance.
_Avoid_: Done, merge

**PR Handoff**:
The point where Conductor publishes the agent's reviewed work as a pull request and presents it to the human for inspection in the same Workspace. PR Handoff requires a non-empty diff and a clean commit path; configured tests must pass when present, while missing tests must be called out as an untested handoff. PR Handoff does not complete the Task and does not imply merge approval.
_Avoid_: Done, merge

**Project Test Command**:
The configured command that Conductor may run to evaluate a Task in its assigned worktree. Conductor may run a Project Test Command during Review, but it should not invent a test command when none is configured.
_Avoid_: Guessing tests, implicit tests

## Flagged Ambiguities

**Vault**:
Use **Agent Control Room** for the operating space and **Obsidian vault** only when talking about the Markdown storage location.

**Session**:
Use **Run** for an agent attempt and **Workspace** for the live cmux surface where that attempt is observed.

## Example Dialogue

Developer: "The Task is ready. Start a new Run in a Workspace and keep the Agent Control Room updated with Workspace Activity."

Domain expert: "The Watch Daemon observes and reconciles, but the coding agent and its skill trigger explicit work updates. Show the current activity as Workspace Activity and the recent sequence in an Activity Timeline. When blocked work includes a specific human question, send a Notification and move the Task to a Human Gate. Do not call the Task Done."

Developer: "After the Run reaches Review, Conductor can run configured tests, perform PR Handoff, and surface the pull request, but the human still decides whether the Task is complete."
