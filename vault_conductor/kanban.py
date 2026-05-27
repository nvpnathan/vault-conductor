from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .constants import BOARD_COLUMNS


@dataclass
class KanbanCard:
    task_id: str
    line: str
    checked: bool
    note_path: str | None = None
    title: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class KanbanColumn:
    title: str
    raw_heading: str
    cards: list[KanbanCard] = field(default_factory=list)
    other_lines: list[str] = field(default_factory=list)


@dataclass
class KanbanBoard:
    frontmatter: str = ""
    preamble: str = ""
    columns: list[KanbanColumn] = field(default_factory=list)
    settings_block: str = ""


@dataclass
class LocatedCard:
    column_title: str
    card: KanbanCard


def parse_board(content: str) -> KanbanBoard:
    normalized = content.replace("\r\n", "\n")
    main, settings = split_settings_block(normalized)
    frontmatter, rest = split_frontmatter(main)
    matches = list(re.finditer(r"^## .+$", rest, flags=re.MULTILINE))
    if not matches:
        return KanbanBoard(frontmatter=frontmatter, preamble=rest, settings_block=settings)

    preamble = rest[: matches[0].start()]
    columns: list[KanbanColumn] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(rest)
        raw_heading = match.group(0)
        body = rest[match.end() : next_start]
        lines = body.lstrip("\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        cards: list[KanbanCard] = []
        other_lines: list[str] = []
        for line in lines:
            card = parse_card_line(line)
            if card:
                cards.append(card)
            else:
                other_lines.append(line)
        columns.append(
            KanbanColumn(
                title=raw_heading.replace("##", "", 1).strip(),
                raw_heading=raw_heading,
                cards=cards,
                other_lines=other_lines,
            )
        )
    return KanbanBoard(frontmatter=frontmatter, preamble=preamble, columns=columns, settings_block=settings)


def split_settings_block(content: str) -> tuple[str, str]:
    match = re.search(r"^%% kanban:settings[\s\S]*$", content, flags=re.MULTILINE)
    if not match:
        return content, ""
    main = content[: match.start()].rstrip() + "\n"
    return main, content[match.start() :]


def split_frontmatter(content: str) -> tuple[str, str]:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", content
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[: index + 1]), "".join(lines[index + 1 :])
    return "", content


def parse_card_line(line: str) -> KanbanCard | None:
    task = re.match(r"^\s*-\s+\[( |x|X)\]\s+(.+)$", line)
    if not task:
        return None
    task_id_match = re.search(r"AGT-\d{4,}", line)
    if not task_id_match:
        return None
    wikilink = re.search(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]", line)
    tags = re.findall(r"#[A-Za-z0-9_/-]+", line)
    return KanbanCard(
        task_id=task_id_match.group(0),
        line=line,
        checked=task.group(1).lower() == "x",
        note_path=wikilink.group(1) if wikilink else None,
        title=wikilink.group(2) if wikilink and wikilink.lastindex and wikilink.group(2) else None,
        tags=tags,
    )


def render_board(board: KanbanBoard) -> str:
    parts: list[str] = []
    if board.frontmatter.strip():
        parts.extend([board.frontmatter.strip(), ""])
    if board.preamble.strip():
        parts.extend([board.preamble.strip(), ""])
    for column in board.columns:
        parts.extend([column.raw_heading or f"## {column.title}", ""])
        parts.extend(card.line for card in column.cards)
        parts.extend(line for line in column.other_lines if line.strip())
        parts.append("")
    if board.settings_block:
        parts.append(board.settings_block.lstrip("\n"))
    rendered = "\n".join(parts)
    rendered = re.sub(r"\n{4,}", "\n\n\n", rendered)
    return rendered if rendered.endswith("\n") else f"{rendered}\n"


def empty_board_content(columns: Iterable[str] = BOARD_COLUMNS) -> str:
    lines = [
        "---",
        "kanban-plugin: board",
        "---",
        "",
        "# Agent Control Room",
        "",
        "This board is managed by `conductor`. You may drag cards manually, but run `conductor sync` if task notes and board state drift.",
        "",
    ]
    for column in columns:
        lines.extend([f"## {column}", ""])
    lines.extend(["%% kanban:settings", "```", '{"kanban-plugin":"board"}', "```", "%%", ""])
    return "\n".join(lines)


def find_card(board: KanbanBoard, task_id: str) -> LocatedCard | None:
    for column in reversed(board.columns):
        for card in column.cards:
            if card.task_id == task_id:
                return LocatedCard(column.title, card)
    return None


def add_card(board: KanbanBoard, column_title: str, card_line: str) -> None:
    card = parse_card_line(card_line)
    if not card:
        raise ValueError(f"Invalid Kanban task card: {card_line}")
    require_column(board, column_title).cards.append(card)


def remove_card(board: KanbanBoard, task_id: str) -> None:
    for column in board.columns:
        column.cards = [card for card in column.cards if card.task_id != task_id]


def move_card(
    board: KanbanBoard,
    task_id: str,
    target_column_title: str,
    *,
    status: str | None = None,
    checked: bool | None = None,
    card_line: str | None = None,
) -> None:
    target = require_column(board, target_column_title)
    located = find_card(board, task_id)
    source_line = card_line or (located.card.line if located else None)
    if source_line is None:
        raise ValueError(f"Cannot move {task_id}; no card exists and no card line was supplied.")
    source_line = update_card_line(source_line, status=status, checked=checked)
    remove_card(board, task_id)
    card = parse_card_line(source_line)
    if not card:
        raise ValueError(f"Invalid Kanban task card after update: {source_line}")
    target.cards.append(card)


def build_card_line(task) -> str:
    checked = "x" if task.status == "done" else " "
    filename = f"{task.id} {task.title}.md"
    return (
        f"- [{checked}] [[20 Agent Tasks/{filename}|{task.id} {task.title}]] "
        f"#repo/{task.repo} #agent/{task.agent} #priority/{task.priority} #risk/{task.risk} #state/{task.status}"
    )


def update_card_line(
    line: str,
    *,
    status: str | None = None,
    checked: bool | None = None,
    task=None,
    tags: list[str] | None = None,
) -> str:
    updated = line
    if checked is not None:
        updated = re.sub(r"^(\s*-\s+\[)( |x|X)(\])", rf"\g<1>{'x' if checked else ' '}\3", updated)
    tags_to_set: list[str] = []
    if status:
        tags_to_set.append(f"#state/{status}")
    if task is not None:
        tags_to_set.extend(
            [
                f"#repo/{task.repo}",
                f"#agent/{task.agent}",
                f"#priority/{task.priority}",
                f"#risk/{task.risk}",
                f"#state/{task.status}",
            ]
        )
    if tags:
        tags_to_set.extend(tags)
    return update_tags(updated, tags_to_set) if tags_to_set else updated


def update_tags(line: str, tags_to_set: list[str]) -> str:
    families = {tag_family(tag) for tag in tags_to_set}
    parts = [
        part
        for part in line.split()
        if not (part.startswith("#") and tag_family(part) in families)
    ]
    return f"{' '.join(parts)} {' '.join(tags_to_set)}".strip()


def tag_family(tag: str) -> str:
    return tag.lstrip("#").split("/", 1)[0]


def require_column(board: KanbanBoard, title: str) -> KanbanColumn:
    for column in board.columns:
        if column.title == title:
            return column
    column = KanbanColumn(title=title, raw_heading=f"## {title}")
    board.columns.append(column)
    return column


def ensure_columns(board: KanbanBoard, columns: Iterable[str] = BOARD_COLUMNS) -> KanbanBoard:
    ordered = list(columns)
    for title in ordered:
        require_column(board, title)
    board.columns.sort(
        key=lambda column: ordered.index(column.title) if column.title in ordered else len(ordered)
    )
    return board
