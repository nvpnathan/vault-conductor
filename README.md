# vault-conductor

A standalone orchestrator that bridges an Obsidian kanban board to [cmux](https://cmux.app) workspaces — watching for cards dragged to **In Progress** and automatically spawning a cmux workspace with Claude Code running inside.

## How It Works

Three layers work together:

| Layer | Tool | Role |
|-------|------|------|
| Control plane | Obsidian vault | Track projects, status, and goals on a kanban board |
| Bridge | `main.py` | Watches the kanban, manages workspaces |
| Execution | cmux + Claude Code | Runs agents, surfaces notifications |

**The workflow:**

1. Create a project note with a `repo` path and `goal`
2. Add it as a card on the kanban board under **Backlog**
3. Drag the card to **In Progress**
4. vault-conductor detects the change and opens a new cmux workspace, `cd`'d into the repo, with Claude Code already running and the project note open as a live markdown panel
5. The agent reads its goal from the note, does the work, and signals completion
6. The card moves to **Review** automatically

## Setup

**Prerequisites:** [cmux](https://cmux.app), [uv](https://docs.astral.sh/uv/), [Claude Code](https://claude.ai/code), and [Obsidian](https://obsidian.md) with the [Kanban plugin](https://github.com/mgmeyers/obsidian-kanban) installed. Python 3.10+ is required.

Install cmux via the Mac App Store or `brew install cmux`.

```bash
# Clone and run setup
git clone https://github.com/nvpnathan/vault-conductor
cd vault-conductor
bash setup.sh
```

Setup installs Python dependencies and wires cmux notification hooks into Claude Code.

**Configure your vault path** — edit the top of `main.py` to point at your Obsidian vault:

```python
VAULT = Path("~/path/to/your/vault").expanduser()
KANBAN = VAULT / "Control Room.md"
PROJECTS = VAULT / "Projects"
```

## Usage

**1. Start the orchestrator** (keep it running in a terminal):

```bash
python main.py
```

**2. Open your vault in Obsidian** and navigate to the kanban board (`Control Room.md`).

**3. Create a project** — duplicate `Templates/New Project.md` into `Projects/Your Project.md` and fill in:

```yaml
---
status: backlog
repo: ~/repos/your-project
goal: "One sentence describing what the agent should do"
agent: cmux claude-teams
priority: high
---
```

**4. Add the card to the kanban board** in `Control Room.md`:

```
- [ ] [[Projects/Your Project]]
```

**5. Drag the card to "In Progress"** — vault-conductor spawns the agent automatically.

## Kanban Columns

| Column | Meaning |
|--------|---------|
| Backlog | Ideas and future work |
| Ready | Defined and ready to run |
| In Progress | Agent actively working |
| Needs Attention | Agent waiting for input |
| Review | Agent finished — review output |
| Done | Human reviewed and accepted |

## Dashboard

A live dashboard of all active agents runs in the cmux Dock sidebar. To run it manually:

```bash
python dashboard.py
```

## Project Structure

```
vault-conductor/
├── main.py          # Kanban watcher + workspace spawner
├── dashboard.py     # Live agent dashboard
└── setup.sh         # One-time setup script
```

## Requirements

- macOS
- Python 3.10+
- [cmux](https://cmux.app) — terminal multiplexer with workspace and notification APIs (`brew install cmux` or Mac App Store)
- [Claude Code](https://claude.ai/code) — AI coding agent CLI
- [uv](https://docs.astral.sh/uv/) — Python package manager
- [Obsidian](https://obsidian.md) — markdown editor (for the kanban UI)
- [Obsidian Kanban plugin](https://github.com/mgmeyers/obsidian-kanban) — renders `Control Room.md` as a drag-and-drop kanban board
