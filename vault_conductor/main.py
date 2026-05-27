from __future__ import annotations

from .cli import watch_main


def main() -> int:
    return watch_main()


if __name__ == "__main__":
    raise SystemExit(main())
