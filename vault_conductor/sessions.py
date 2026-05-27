from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import Config
from .markdown import write_file_atomic


def sessions_path(config: Config) -> Path:
    return config.state_root / "sessions.json"


def read_sessions(config: Config) -> dict[str, Any]:
    path = sessions_path(config)
    if not path.exists():
        return {"version": 1, "sessions": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("version", 1)
    data.setdefault("sessions", {})
    return data


def write_sessions(config: Config, data: dict[str, Any]) -> None:
    data.setdefault("version", 1)
    data.setdefault("sessions", {})
    write_file_atomic(sessions_path(config), json.dumps(data, indent=2) + "\n")


def upsert_session(config: Config, task_id: str, record: dict[str, Any]) -> None:
    data = read_sessions(config)
    data["sessions"][task_id] = record
    write_sessions(config, data)


def remove_session(config: Config, task_id: str) -> dict[str, Any] | None:
    data = read_sessions(config)
    record = data["sessions"].pop(task_id, None)
    write_sessions(config, data)
    return record


def transcript_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
