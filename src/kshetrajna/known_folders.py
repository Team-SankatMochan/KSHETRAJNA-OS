"""Discovery of conventional user folders without scanning their contents."""

import os
from pathlib import Path


KNOWN_FOLDER_NAMES = ("Desktop", "Downloads", "Documents", "Pictures", "OneDrive")


def discover_known_folders() -> list[dict]:
    """Return existing conventional folders and their paths.

    This only checks whether the directory itself exists. It never enumerates
    files. The caller must still ask the user to approve a folder before the
    file scanner starts.
    """
    profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
    candidates = []
    for name in KNOWN_FOLDER_NAMES:
        path = profile / name
        if path.is_dir():
            candidates.append({"id": name.casefold(), "label": name, "path": str(path)})

    # OneDrive commonly redirects the known folders into a tenant-specific
    # directory. Add only the conventional child folders and de-duplicate by
    # resolved path; this still performs no content scan.
    for one_drive in (profile / "OneDrive", profile / "OneDrive - Personal"):
        if not one_drive.is_dir():
            continue
        for name in ("Desktop", "Downloads", "Documents"):
            path = one_drive / name
            if path.is_dir() and not any(Path(item["path"]).resolve() == path.resolve() for item in candidates):
                candidates.append({"id": f"onedrive-{name.casefold()}", "label": f"OneDrive {name}", "path": str(path)})
    return candidates
