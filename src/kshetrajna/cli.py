"""Convenience entry point for reviewed Sort AI and cleanup commands."""

import sys

from .jarvis import main as bridge_main


def main() -> int:
    arguments = sys.argv[1:]
    default_command = "sort_preview"
    if arguments and arguments[0] == "sort":
        arguments = arguments[1:]
        if arguments and arguments[0] in {"preview", "apply", "undo"}:
            arguments[0] = "sort_" + arguments[0]
    elif arguments and arguments[0] == "cleanup":
        arguments = arguments[1:]
        default_command = "empty_preview"
        if arguments and arguments[0] in {"empty", "empties"}:
            arguments[0] = "empty_preview"
        elif arguments and arguments[0] in {"duplicate", "duplicates"}:
            arguments[0] = "duplicate_preview"
    if arguments and arguments[0].startswith("-"):
        return bridge_main(arguments, default_command=default_command)
    return bridge_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
