"""Start an installed local model runtime, then the ordinary dashboard launcher."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .local_ai import LocalAIError, OllamaClient


def main():
    from .__main__ import main as dashboard_main
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        return dashboard_main()
    client = OllamaClient()
    models = None
    try:
        models = client.models()
    except LocalAIError:
        conventional = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        executable = str(conventional) if conventional.is_file() else shutil.which("ollama")
        if executable:
            try:
                runtime_env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_NO_CLOUD": "1"}
                subprocess.Popen([executable, "serve"], shell=False,
                                 env=runtime_env,
                                 creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(12):
                    time.sleep(.5)
                    try:
                        models = client.models()
                        break
                    except LocalAIError:
                        continue
            except OSError:
                pass
    if models is None:
        print("Local tools are available. For AI chat, start/install Ollama from https://ollama.com/download and choose a local model.")
    elif not models:
        print("Ollama is ready but no local model is installed. Choose a model in Ollama; this launcher downloads nothing.")
    else:
        print("Local model ready. Open Chat and enable local AI after reviewing consent. No API key is required.")
    return dashboard_main()


if __name__ == "__main__":
    raise SystemExit(main())
