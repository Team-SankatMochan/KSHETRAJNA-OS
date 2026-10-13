"""Windowless packaged entry point with a visible startup error on Windows."""

import ctypes
import sys


def main() -> int:
    try:
        from .chat_launcher import main as launch
        return launch()
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 1
        if code == 0:
            return 0
        message = "Kshetrajna could not start. Check whether another copy is running or port 8765 is in use. Open Console.cmd in the app folder for details."
    except Exception:
        code = 1
        message = "Kshetrajna could not start. Open Console.cmd in the app folder to see the error and check the local diagnostic log."
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "Kshetrajna startup", 0x10)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
