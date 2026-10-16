"""Bounded, portable folder watching. Never executes a file move."""
import os
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .file_scanner import is_sensitive_file

PARTIAL_SUFFIXES = ('.crdownload', '.part', '.partial', '.tmp', '.download', '.opdownload')


class DownloadWatcher:
    def __init__(self, service, *, interval=2, settle_seconds=4):
        self.service = service
        self.interval = interval
        self.settle_seconds = settle_seconds
        self._stop = threading.Event()
        self._thread = None
        self._snapshots = {}
        self._pending = {}
        self._refresh_roots = set()

    def start(self):
        if self.service.demo or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name='download-watch', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.poll()
            except Exception:
                logging.getLogger(__name__).warning('Folder watching could not complete this poll; retrying on the next interval.')

    def _status(self, root_id, message):
        with self.service.store._connect() as db:
            db.execute('UPDATE roots SET watch_message=?,watch_last_check=? WHERE id=?',
                       (message, datetime.now(timezone.utc).isoformat(), root_id))

    def poll(self, *, now=None):
        now = time.monotonic() if now is None else now
        if self.service.demo:
            return
        with self.service._gate:
            if self.service._catalog_deleting:
                return
            roots = [r for r in self.service.store.get_roots()
                     if r.get('watch_enabled') and r['status'] != 'removed']
            active_ids = {r['id'] for r in roots}
            for stale in set(self._snapshots) - active_ids:
                self._snapshots.pop(stale, None)
                self._pending.pop(stale, None)
            for root in roots:
                root_id = root['id']
                if root_id in self.service._active_scans:
                    continue
                try:
                    if root_id in self._refresh_roots and root['status'] in ('ready', 'truncated'):
                        preview = self.service.generate_sort_plan(root_id, True, prefer_existing=True)
                        self._refresh_roots.discard(root_id)
                        self._status(root_id, f"New arrivals checked: {preview['automatic_count']} clear matches, {preview['review_count']} need advice. Review Sort AI to approve moves.")
                        continue
                    base = Path(root['path'])
                    self.service.action_manager._validate_path_safety(base, base)
                    snapshot = {}
                    limit = False
                    with os.scandir(base) as entries:
                        for i, entry in enumerate(entries):
                            if i >= 20000:
                                limit = True
                                break
                            name = entry.name
                            if name.lower().endswith(PARTIAL_SUFFIXES) or entry.is_symlink():
                                continue
                            if is_sensitive_file(name, Path(name).suffix[1:], name):
                                continue
                            info = entry.stat(follow_symlinks=False)
                            if getattr(info, 'st_file_attributes', 0) & 0x400 or not entry.is_file(follow_symlinks=False):
                                continue
                            snapshot[name] = (info.st_size, info.st_mtime_ns)
                    if root_id not in self._snapshots:
                        with self.service.store._connect() as db:
                            indexed = db.execute('SELECT name,size_bytes,modified_at FROM files WHERE root_id=? AND relative_path=name', (root_id,)).fetchall()
                        self._snapshots[root_id] = {row['name']: (row['size_bytes'], int(datetime.fromisoformat(row['modified_at']).timestamp() * 1e9))
                                                    for row in indexed if row['modified_at']}
                        # Indexed timestamps have microsecond precision. Compare on that precision once.
                        self._snapshots[root_id] = {name: signature for name, signature in snapshot.items()
                            if name in self._snapshots[root_id] and signature[0] == self._snapshots[root_id][name][0]
                            and abs(signature[1] - self._snapshots[root_id][name][1]) < 2000}
                    previous = self._snapshots[root_id]
                    pending = self._pending.setdefault(root_id, {})
                    changed = {name: signature for name, signature in snapshot.items() if previous.get(name) != signature}
                    for name in list(pending):
                        if name not in changed:
                            pending.pop(name, None)
                    for name, signature in changed.items():
                        if name not in pending or pending[name][0] != signature:
                            pending[name] = (signature, now)
                    settled = [name for name, (_, since) in pending.items() if now - since >= self.settle_seconds]
                    if settled:
                        with self.service.store._connect() as db:
                            # Keep applied batches undoable, but expire unexecuted previews.
                            db.execute("UPDATE sort_plans SET expires_at=? WHERE status IN ('preview','partial')",
                                       (datetime.now(timezone.utc).isoformat(),))
                        self.service.rescan_root(root_id)
                        self._refresh_roots.add(root_id)
                        self._snapshots[root_id] = snapshot
                        self._pending[root_id] = {}
                        self._status(root_id, f'{len(settled)} settled new/changed file(s) detected. Refreshing index; preview sorting again.')
                    else:
                        idle = root.get('watch_message', '')
                        if 'New arrivals checked:' not in idle:
                            idle = 'Watching for new files. Subfolders are checked by Rescan.'
                        self._status(root_id, ('Watch limited to 20,000 immediate entries. ' if limit else '') +
                            (f'Waiting for {len(pending)} file(s) to settle.' if pending else idle))
                except (OSError, ValueError) as error:
                    self._status(root_id, 'Watch unavailable: ' + str(error))
