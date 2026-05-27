from __future__ import annotations

from datetime import datetime, timezone

from .config import Config


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def append_operational_log(config: Config, component: str, message: str) -> None:
    log_file = config.logs_root / "conductor-watch.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{now_iso()} {component} {message}\n")
