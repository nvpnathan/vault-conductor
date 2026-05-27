TASK_STATUSES = [
    "backlog",
    "ready",
    "running",
    "needs-human",
    "review-diff",
    "needs-revision",
    "pr-opened",
    "done",
    "failed",
    "parked",
]

DEFAULT_COLUMNS = {
    "backlog": "Backlog",
    "ready": "Ready",
    "running": "Running",
    "needs-human": "Needs Human",
    "review-diff": "Review Diff",
    "needs-revision": "Needs Revision",
    "pr-opened": "PR Opened",
    "done": "Done",
    "failed": "Failed / Parked",
    "parked": "Failed / Parked",
}

BOARD_COLUMNS = [
    "Backlog",
    "Ready",
    "Running",
    "Needs Human",
    "Review Diff",
    "Needs Revision",
    "PR Opened",
    "Done",
    "Failed / Parked",
]

TASK_BODY_HEADINGS = [
    "Goal",
    "Acceptance criteria",
    "Context",
    "Agent instructions",
    "Human question",
    "Current status",
    "Log",
    "Diff summary",
    "Test output",
    "Decision",
    "Runs",
    "Links",
]
