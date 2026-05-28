# Automatically Create Pull Requests During Handoff

When a Task reaches Review and satisfies the configured automation gates, Vault Conductor should create the pull request and present it to the human instead of stopping at a manual PR command. This keeps the Agent Control Room focused on reviewing finished agent output while preserving the human-only boundary for completion, merge, and cleanup.
