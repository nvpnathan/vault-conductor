from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def parse_markdown(content: str) -> tuple[dict[str, Any], str]:
    normalized = content.replace("\r\n", "\n")
    lines = normalized.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                raw = "".join(lines[1:index])
                body = "".join(lines[index + 1 :]).lstrip("\n")
                data = yaml.safe_load(raw) or {}
                return data, body
    return {}, normalized


def stringify_markdown(frontmatter: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    clean_body = body.lstrip("\n")
    return f"---\n{yaml_text}\n---\n{clean_body}"


def write_file_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def replace_section(body: str, heading: str, content: str) -> str:
    lines = body.replace("\r\n", "\n").split("\n")
    heading_line = f"# {heading}"
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == heading_line)
    except StopIteration:
        suffix = "" if body.endswith("\n") else "\n"
        return f"{body}{suffix}\n{heading_line}\n\n{content.strip()}\n"
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("# "):
            end = index
            break
    lines[start:end] = [heading_line, "", content.strip(), ""]
    return "\n".join(lines).replace("\n\n\n\n", "\n\n\n")


def append_section_line(body: str, heading: str, line: str) -> str:
    lines = body.replace("\r\n", "\n").split("\n")
    heading_line = f"# {heading}"
    try:
        start = next(index for index, candidate in enumerate(lines) if candidate.strip() == heading_line)
    except StopIteration:
        return f"{body.rstrip()}\n\n{heading_line}\n\n- {line}\n"
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("# "):
            end = index
            break
    lines.insert(end, f"- {line}")
    return "\n".join(lines)
