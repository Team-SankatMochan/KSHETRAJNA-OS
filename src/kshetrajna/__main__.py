"""Launch the local collector and dashboard."""

import argparse
import logging
import sys
import webbrowser
import tempfile
from pathlib import Path
from logging.handlers import RotatingFileHandler

from .config import ConfigStore, default_data_dir
from .collector import Collector
from .server import DashboardServer
from .storage import EventStore
from .telemetry import WindowsProbe
from .demo import DemoProbe, seed_demo
from .instance import InstanceLock
from .file_storage import FileStore
from .file_service import FileService
from .file_demo import seed_file_demo


def main() -> int:
    parser = argparse.ArgumentParser(description="Kshetrajna: local adaptive workload intelligence")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Synthetic demo, isolated from Windows and your history")
    parser.add_argument("--data-dir", type=Path, help="Override local live-data directory")
    args = parser.parse_args()
    if sys.platform != "win32" and not args.demo:
        parser.error("Windows is required for telemetry")
    port = args.port or (8766 if args.demo else 8765)
    if not 1024 <= port <= 65535:
        parser.error("Port must be between 1024 and 65535")

    if args.demo and args.data_dir:
        parser.error("Demo uses temporary isolated storage; omit --data-dir")
    temporary = tempfile.TemporaryDirectory(prefix="kshetrajna-demo-") if args.demo else None
    directory = Path(temporary.name) if temporary else (args.data_dir or default_data_dir())
    directory.mkdir(parents=True, exist_ok=True)
    try:
        instance = InstanceLock(directory)
    except RuntimeError as error:
        parser.error(str(error))
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    log_handler = RotatingFileHandler(directory / "kshetrajna.log", maxBytes=500_000, backupCount=2, encoding="utf-8")
    log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(log_handler)
    config = ConfigStore(directory)
    if args.demo:
        config.update({"enabled": True, "sample_interval_seconds": 2})
    try:
        settings = config.load()
    except (OSError, ValueError) as error:
        parser.error(f"Settings could not be loaded: {error}")
    store = EventStore(directory / "events.db")
    file_store = FileStore(directory / "files.db")
    probe = DemoProbe() if args.demo else WindowsProbe()
    if args.demo:
        seed_demo(store, probe)
        seed_file_demo(file_store)
    file_service = FileService(file_store, demo=args.demo)
    collector = Collector(probe, config, store)
    try:
        server = DashboardServer(port, collector, config, store, demo=args.demo)
        server.file_service = file_service
        server.service.file_service = file_service
    except OSError as error:
        parser.error(f"Could not open dashboard: {error}")
    server.service.restore_all()
    collector.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    logger.info("Dashboard started on loopback port %s", server.server_port)
    print(f"Kshetrajna dashboard: {url}")
    print("SYNTHETIC DEMO — no Windows modifications." if args.demo else "LIVE — all recommendations require your approval.")
    print("Collection is enabled." if settings.enabled else "Collection is paused until you enable it in the dashboard.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
        server.service.restore_all()
        server.server_close()
        logger.info("Dashboard stopped")
        logger.removeHandler(log_handler)
        log_handler.close()
        instance.close()
        if temporary:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
