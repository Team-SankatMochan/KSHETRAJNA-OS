"""Convenience entry point for ``kshetrajna-sort``."""

import sys

from .jarvis import main as bridge_main


def main() -> int:
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "sort":
        arguments = arguments[1:]
    if not arguments or arguments[0].startswith("-"):
        arguments = ["sort_preview", *arguments]
    return bridge_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
