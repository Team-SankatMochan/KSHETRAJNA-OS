"""Deterministic, reviewable file-organization suggestions.

The first Sort AI provider is intentionally rule-based.  It uses metadata and
the bounded excerpt already present in the approved file catalog; it does not
pretend to be a generative model.  Every returned destination is still treated
as untrusted input by the file-action safety checks before a mutation.
"""

from pathlib import Path

from .file_insights import analyze_catalog, DOWNLOAD_FOLDERS
from .file_scanner import is_sensitive_file


CATEGORY_FOLDERS = {
    "Code": "Code",
    "Data": "Data",
    "Media": "Media",
    "Document": "Documents",
}

def automation_eligible(suggestion: dict) -> bool:
    """Only clear, unblocked loose-file moves enter the automatic batch."""
    return (suggestion.get('status') == 'suggested'
            and not suggestion.get('review_required')
            and suggestion.get('confidence', 0) >= 88
            and '/' not in suggestion.get('source_relative', '').replace('\\', '/'))


def build_suggestions(files: list[dict], root_path: Path, *, create_folders: bool = False,
                      inspect_filesystem: bool = True, include_nested: bool = False) -> list[dict]:
    """Build bounded organization proposals from indexed files.

    Suggestions never overwrite a destination.  Missing destination folders
    are marked as blocked unless the user explicitly requested folder creation
    for this plan.  Sensitive files remain searchable but are never proposed
    for movement by this organizer.
    """
    existing = {str(row["relative_path"]).replace('\\', '/').lower() for row in files}
    analyzed = analyze_catalog(files)
    suggestions: list[dict] = []
    for file_row, insight in zip(files, analyzed):
        name = str(file_row.get("name", ""))
        relative = str(file_row.get("relative_path", "")).replace('\\', '/')
        extension = str(file_row.get("extension", "")).lower()
        parts = relative.split('/')
        if not name or not relative or is_sensitive_file(name, extension, relative):
            continue
        loose = len(parts) == 1
        installer = extension in ('exe', 'msi', 'msix') and any(
            word in name.casefold() for word in ('setup', 'install'))
        if not loose and not insight['review_required'] and not include_nested:
            continue
        unknown = extension not in DOWNLOAD_FOLDERS and insight['content_kind'] in ('document', 'binary') and not insight['content_inspected'] and not installer
        if unknown:
            insight = {**insight, 'review_required': True,
                       'review_reason': 'The file type or purpose is unclear; choose a destination or keep it here.',
                       'decision_confidence': 'low'}
        destination = insight.get("suggested_destination") or f"{insight.get('suggested_folder') or CATEGORY_FOLDERS.get(insight['suggested_category'], 'Documents')}/{name}"
        folder = destination.rsplit('/', 1)[0] if '/' in destination else ""
        if relative.lower() == destination.lower():
            if not insight['review_required']:
                continue
            blocked_reason = insight['review_reason'] or 'This file needs your decision before moving.'
        else:
            blocked_reason = None

        destination_path = root_path / folder
        # Check both the catalog and the current filesystem.  A user may have
        # created a destination after the last scan, so the catalog alone is
        # not enough to prevent an overwrite.
        conflict = relative.casefold() != destination.casefold() and (
            destination.lower() in existing or (inspect_filesystem and (root_path / destination).exists()))
        folder_exists = inspect_filesystem and destination_path.is_dir()
        selected = True
        if conflict:
            blocked_reason = "A file already exists at the proposed destination"
            selected = False
        elif not folder_exists and not create_folders:
            blocked_reason = "Destination folder does not exist; enable folder creation for this plan"
            selected = False
        if blocked_reason:
            selected = False

        confidence = 94 if insight.get("decision_confidence") == "high" else 88 if insight.get("decision_confidence") == "medium" else 78
        reason = (f"Automatic decision: {insight.get('decision_summary', insight['content_summary'])}. "
                  f"{insight['content_summary']} This supports placing it in {folder}.")
        if insight["tags"]:
            reason += " Tags: " + ", ".join(insight["tags"][:3]) + "."
        if insight["review_required"]:
            reason += " " + insight["review_reason"]
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
            "content_summary": insight["content_summary"],
            "content_inspected": insight["content_inspected"],
            "review_required": insight["review_required"],
            "review_reason": insight["review_reason"],
            "recommended_action": insight["recommended_action"],
            "decision_confidence": insight["decision_confidence"],
            "decision_evidence": insight["decision_evidence"],
            "suggested_folder": folder,
        })
    return suggestions
