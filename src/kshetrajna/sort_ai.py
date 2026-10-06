"""Deterministic, reviewable file-organization suggestions.

The first Sort AI provider is intentionally rule-based.  It uses metadata and
the bounded excerpt already present in the approved file catalog; it does not
pretend to be a generative model.  Every returned destination is still treated
as untrusted input by the file-action safety checks before a mutation.
"""

from pathlib import Path

from .file_insights import generate_insights
from .file_scanner import SENSITIVE_DIRS, SENSITIVE_NAMES, SENSITIVE_EXTENSIONS


CATEGORY_FOLDERS = {
    "Code": "Code",
    "Data": "Data",
    "Media": "Media",
    "Document": "Documents",
}


def build_suggestions(files: list[dict], root_path: Path, *, create_folders: bool = False) -> list[dict]:
    """Build bounded organization proposals from indexed files.

    Suggestions never overwrite a destination.  Missing destination folders
    are marked as blocked unless the user explicitly requested folder creation
    for this plan.  Sensitive files remain searchable but are never proposed
    for movement by this organizer.
    """
    existing = {str(row["relative_path"]).replace('\\', '/').lower() for row in files}
    suggestions: list[dict] = []
    for file_row in files:
        name = str(file_row.get("name", ""))
        relative = str(file_row.get("relative_path", "")).replace('\\', '/')
        extension = str(file_row.get("extension", "")).lower()
        parts = relative.split('/')
        if not name or not relative or any(part.lower() in SENSITIVE_DIRS for part in parts[:-1]):
            continue
        if name.lower() in SENSITIVE_NAMES or extension in SENSITIVE_EXTENSIONS:
            continue

        insight = generate_insights(name, extension, relative, str(file_row.get("excerpt", "")))
        folder = CATEGORY_FOLDERS.get(insight["suggested_category"], "Documents")
        destination = f"{folder}/{name}"
        if relative.lower() == destination.lower():
            continue

        destination_path = root_path / folder
        # Check both the catalog and the current filesystem.  A user may have
        # created a destination after the last scan, so the catalog alone is
        # not enough to prevent an overwrite.
        conflict = destination.lower() in existing or (root_path / destination).exists()
        folder_exists = destination_path.is_dir()
        blocked_reason = None
        selected = True
        if conflict:
            blocked_reason = "A file already exists at the proposed destination"
            selected = False
        elif not folder_exists and not create_folders:
            blocked_reason = "Destination folder does not exist; enable folder creation for this plan"
            selected = False

        confidence = 92 if insight["suggested_category"] in {"Code", "Data", "Media"} else 78
        reason = f"The .{extension} metadata rule places this file in {folder}."
        if insight["tags"]:
            reason += " Tags: " + ", ".join(insight["tags"][:3]) + "."
        if blocked_reason:
            reason += " " + blocked_reason + "."

        suggestions.append({
            "file_id": file_row.get("id"),
            "source_relative": relative,
            "destination_relative": destination,
            "reason": reason,
            "confidence": confidence,
            "selected": selected,
            "status": "blocked" if blocked_reason else "suggested",
            "blocked_reason": blocked_reason,
            "category": insight["suggested_category"],
            "tags": insight["tags"],
        })
    return suggestions
