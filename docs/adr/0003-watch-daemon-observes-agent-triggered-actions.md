# Keep The Watch Daemon Observational

`conductor watch` should be the single always-running daemon, but it should primarily observe, reconcile, and surface state instead of silently deciding the next workflow action. Activity updates, tests, and PR handoff should be triggered explicitly by the coding agent through the Agent Control Room skill and conductor commands, making automation visible in the agent transcript and keeping the daemon predictable.
