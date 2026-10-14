"""Evidence-based local file purpose and next-action decisions."""

import csv
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
    "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
    "LPT6", "LPT7", "LPT8", "LPT9",
})
CODE_EXTENSIONS = frozenset({
    "py", "js", "ts", "jsx", "tsx", "html", "css", "scss", "java", "c", "h",
    "cc", "cpp", "cxx", "hpp", "cs", "go", "rs", "php", "rb", "swift", "kt",
    "kts", "sql", "sh", "bash", "ps1", "bat",
})
DATA_EXTENSIONS = frozenset({"csv", "tsv", "xlsx", "sqlite", "db", "json", "yaml", "yml", "toml"})
MEDIA_EXTENSIONS = frozenset({'jpg','jpeg','png','svg','gif','webp','bmp','heic','tif','tiff','mp4','mov','avi','mkv','webm','wmv','m4v','wav','mp3','flac','aac','m4a','ogg','opus'})
DOWNLOAD_FOLDERS = {
    **dict.fromkeys(('jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'heic', 'tif', 'tiff', 'svg'), 'Pictures'),
    **dict.fromkeys(('mp4', 'mkv', 'mov', 'avi', 'webm', 'wmv', 'm4v'), 'Videos'),
    **dict.fromkeys(('mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg', 'opus'), 'Audio'),
    **dict.fromkeys(('zip', '7z', 'rar', 'tar', 'gz', 'bz2', 'xz'), 'Archives'),
    **dict.fromkeys(('ppt', 'pptx', 'odp'), 'Presentations'),
    **dict.fromkeys(('xls', 'xlsx', 'ods'), 'Data/Spreadsheets'),
    **dict.fromkeys(('doc', 'docx', 'odt', 'rtf', 'pdf', 'epub'), 'Documents'),
}
OFFICE_EXTENSIONS = frozenset({"docx", "pptx", "xlsx"})
PDF_EXTENSIONS = frozenset({"pdf"})
GENERATED_EXTENSIONS = frozenset({"exe", "dll", "msi", "pyc", "class", "obj", "o", "so", "bin", "tmp", "bak"})
LOG_EXTENSIONS = frozenset({"log", "out", "trace"})
PROJECT_MARKERS = frozenset({
    "package.json", "pyproject.toml", "requirements.txt", "cargo.toml", "go.mod",
    "cmakelists.txt", "makefile", "pom.xml", "build.gradle", ".gitignore",
})

# Specific subjects outrank generic study terms; matches are whole phrases.
SUBJECTS = (
    ("Deep Learning", ("deep learning", "neural network", "neural networks", "back propagation",
                       "backpropagation", "forward propagation", "propagation activations")),
    ("Data Structures", ("data structures", "algorithms", "dsa", "linked list", "binary tree")),
    ("Formal Languages", ("formal languages", "automata", "flat assignment", "finite automaton")),
    ("Software Design", ("uml", "class diagram", "use case", "software engineering")),
    ("Machine Learning", ("machine learning", "regression", "classification model")),
    ("Mathematics", ("calculus", "linear algebra", "probability", "statistics")),
)
STUDY_TERMS = ("course plan", "syllabus", "lecture", "midsem", "mid semester", "question paper",
               "sample questions", "assignment", "semester", "exam", "tutorial")


def _normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _matches(text: str, phrases) -> list[str]:
    padded = " " + _normalized(text) + " "
    return [phrase for phrase in phrases if " " + phrase + " " in padded]


def clean_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', '', name).rstrip(' .')
    if Path(cleaned).stem.upper() in RESERVED_NAMES:
        cleaned = f"local_{cleaned}"
    return cleaned or "unnamed_file"


def _profile(name: str, ext: str, text: str, size: int | None) -> dict:
    """Describe format evidence, including incomplete previews honestly."""
    stripped = text.strip()
    inner_ext = Path(Path(name).stem).suffix.lstrip('.').casefold() if ext == "txt" else ""
    code_ext = inner_ext if inner_ext in CODE_EXTENSIONS else ext
    category, kind, summary = "Document", "document", "Document; no readable text was indexed."
    if ext in MEDIA_EXTENSIONS:
        category, kind = "Media", "media"
        summary = "Media file; visual/audio meaning has not been inferred from its name."
    elif ext in {'zip', '7z', 'rar', 'tar', 'gz', 'bz2', 'xz'}:
        kind, summary = 'archive', 'Archive format inferred from the extension; contents were not extracted.'
    elif ext in GENERATED_EXTENSIONS:
        category, kind = "Code", "binary"
        summary = "Binary, compiled output, installer, or backup; its purpose needs path/source context."
    elif ext in OFFICE_EXTENSIONS:
        category = "Data" if ext == "xlsx" else "Document"
        kind = {"docx": "Office document", "pptx": "slide presentation", "xlsx": "spreadsheet"}[ext]
        summary = f"{kind.capitalize()} with an extracted text preview." if stripped else f"{kind.capitalize()}; no text could be extracted."
    elif ext in PDF_EXTENSIONS:
        category, kind = "Document", "PDF document"
        summary = "PDF document with a bounded text preview." if stripped else "PDF document; use the in-dashboard document viewer."
    elif ext in {"csv", "tsv"} and stripped:
        category, kind = "Data", "tabular data"
        try:
            rows = list(csv.reader(io.StringIO(stripped), delimiter='\t' if ext == "tsv" else ','))
            columns = rows[0] if rows else []
            summary = f"tabular data: {len(columns)} columns ({', '.join(columns[:6])[:160]}); {max(0, len(rows) - 1)} preview rows."
        except csv.Error:
            summary = "Tabular data; the excerpt is partial."
    elif ext == "json" and stripped:
        category, kind = "Data", "structured data"
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                summary = "JSON object with fields: " + ", ".join(str(key) for key in list(value)[:8])[:180] + "."
            elif isinstance(value, list):
                summary = f"JSON list with {len(value)} items in this preview."
            else:
                summary = "JSON scalar value."
        except (ValueError, RecursionError):
            summary = "Partial JSON preview; a bounded excerpt cannot establish whether the full file is valid."
    elif ext in LOG_EXTENSIONS:
        category, kind, summary = "Data", "log", "Diagnostic/event log preview."
    elif code_ext in CODE_EXTENSIONS or re.search(
        r"(?m)^\s*(?:#include\s*[<\"]|(?:export\s+)?(?:async\s+)?function\s+\w+|"
        r"(?:class|def|import|from)\s+\w+|(?:const|let|var)\s+\w+\s*=|<!doctype|<html)", text, re.I
    ):
        category, kind = "Code", "source code"
        summary = f"{code_ext.upper() + ' ' if code_ext in CODE_EXTENSIONS else ''}source code in the indexed preview."
    elif ext in {"yaml", "yml", "toml", "ini", "xml"}:
        category, kind, summary = "Code", "configuration", "Structured configuration or markup preview."
    elif stripped:
        first_line = next((line.strip().lstrip('#').strip() for line in text.splitlines() if line.strip()), '')
        summary = "Text preview: " + first_line[:180]
    if size == 0 and not stripped:
        kind, summary = "empty", "Zero-byte file; no content."
    return {"suggested_category": category, "content_kind": kind, "content_summary": summary,
            "content_inspected": bool(stripped)}


def generate_insights(file_name: str, extension: str, relative_path: str, excerpt: str,
                      *, size_bytes: int | None = None, context: dict | None = None) -> dict:
    """Automatically choose a purpose, destination, and next action with evidence.

    Decisions are recommendations. Name suffixes and matching excerpts are
    never treated as proof of identical complete file contents.
    """
    context = context or {}
    ext = extension.casefold().lstrip('.')
    relative = relative_path.replace('\\', '/')
    path = PurePosixPath(relative)
    text = str(excerpt or '')
    profile = _profile(file_name, ext, text, size_bytes)
    evidence = [profile["content_summary"]]
    purpose = profile["content_kind"].capitalize()
    topic = ""
    folder = {"Code": "Code", "Data": "Data", "Media": "Media", "Document": "Documents"}[profile["suggested_category"]]
    action, confidence, usefulness = "organize", "medium", "potentially useful"
    reason = "Keep the content and group it by its detected purpose."
    labels = file_name + " " + relative + " " + str(context.get("root_label", ''))
    content_signals = text[:4096]
    study = _matches(labels + " " + content_signals, STUDY_TERMS)
    for subject, terms in SUBJECTS:
        name_hits, content_hits = _matches(labels, terms), _matches(content_signals, terms)
        if name_hits or content_hits:
            topic = subject
            evidence.append(f"Subject evidence: {', '.join(dict.fromkeys(name_hits + content_hits))}.")
            confidence = "high" if content_hits and name_hits else "medium"
            break
    if study or (topic and profile["suggested_category"] == "Document"):
        purpose = "Study material"
        folder = "Study/" + (topic or "Course Materials")
        usefulness, confidence = "valuable study/reference material", "high" if study and topic else "medium"
        reason = "Keep this course/reference material; organize it by subject, not as disposable sample data."
        if study:
            evidence.append("Study evidence: " + ", ".join(study[:4]) + ".")

    audit_terms = _matches(labels + " " + content_signals, ("gtm", "tracking audit", "audit", "tag manager", "datalayer", "rest clients discovery"))
    if audit_terms and not study:
        purpose, topic, usefulness = "Project audit evidence", "Tracking Audit", "project evidence"
        folder, action, confidence = "Projects/Tracking Audit", "keep", "high"
        reason = "Keep this audit report or captured source beside its related project files."
        evidence.append("Audit evidence: " + ", ".join(audit_terms[:4]) + ".")

    if profile["content_kind"] == "media":
        action, usefulness, reason = "keep", "personal media; value cannot be inferred", "Keep this media; a numbered download is not proof of a duplicate."
        if _matches(file_name, ("whatsapp",)):
            purpose, topic, folder = "Messaging video/photo", "WhatsApp", "Media/WhatsApp"
            evidence.append("The filename identifies a WhatsApp export.")
        elif _matches(file_name, ("contact sheet",)):
            purpose, topic, folder = "Contact sheet / reference image", "Reference Images", "Media/Reference"
            reason = "Keep this likely reference image with its guide or project; inspect the image preview to check its contents."
            evidence.append("The filename suggests a contact sheet; this is filename evidence, not visual recognition.")

    parent_parts = [part.casefold() for part in path.parts[:-1]]
    cache = ext == "pyc" and "__pycache__" in parent_parts
    paired = context.get("paired_source")
    if cache:
        purpose, topic, usefulness = "Python bytecode cache", "Generated Cache", "regenerable cache"
        action, folder, confidence = "cleanup", "", "high"
        reason = "Remove this regenerable Python cache when the application is not using it; preserve the .py source."
        evidence.append(".pyc file inside __pycache__; Python normally recreates this cache.")
    elif paired and ext in GENERATED_EXTENSIONS:
        purpose, action, usefulness, confidence = "Build output", "keep", "generated output linked to source", "high"
        reason = "Keep this output beside its source; moving it to Documents may break the project layout."
        evidence.append(f"Related source in the catalog: {paired}.")
    elif ext in {"exe", "msi"} and _matches(file_name, ("setup", "installer", "heroku", "install")):
        purpose, action, folder = "Installer or developer tool", "keep", "Tools/Installers"
        reason = "Keep until you know the tool is installed or no longer needed; an .exe is not necessarily disposable."
    elif ext in GENERATED_EXTENSIONS:
        purpose, action = "Binary or generated artifact", "keep"
        reason = "Keep the source file and this artifact until you know it can be regenerated safely."

    # Preserve project layouts and nested collections instead of flattening
    # their source, configuration, reports, and dependent files by extension.
    project = context.get("project_folder")
    if project and action != "cleanup" and profile["content_kind"] not in {"log", "binary", "media"}:
        action = "keep"
        folder = str(path.parent) if str(path.parent) != '.' else ""
        reason = f"Keep this file in {project}; its project context is stronger evidence than a generic category folder."
        evidence.append(f"Project context: {project}.")
    elif len(path.parts) > 1 and (profile['suggested_category'] in {'Code', 'Data'} or profile['content_kind'] == 'archive') and action == "organize":
        action, folder = "keep", str(path.parent)
        reason = "Keep this file in its existing collection/project rather than separating it from related files."

    if profile["content_kind"] == "log" and not audit_terms:
        purpose, action, folder = "Diagnostic log", "archive", "Archive/Logs"
        reason = "Archive this log rather than delete it; it may still explain a failure."
    # Loose downloads can be grouped by a clear format even when their value
    # or subject cannot be inferred. Preserve existing collections/projects.
    if len(path.parts) == 1 and not project and not paired and action not in {'cleanup', 'archive'}:
        format_folder = DOWNLOAD_FOLDERS.get(ext)
        if ext in ('exe', 'msi', 'msix') and any(word in file_name.casefold() for word in ('setup', 'install')):
            format_folder = 'Installers'
        if format_folder and (not topic or profile['content_kind'] == 'media') and profile['content_kind'] != 'empty':
            folder, action, confidence = format_folder, 'organize', 'high'
            reason = f'Group this loose {ext.upper()} download in {folder}; preserve its contents.'
            evidence.append(f'Clear format destination: {folder}; file extension is {ext}.')
    # A marker is evidence of unfinished work only when it is a standalone
    # note/comment, not an HTML placeholder attribute or JSON report field.
    markers = re.findall(r"(?im)^\s*(?:#|//|/\*|\*|-)?\s*(TODO|FIXME)\s*[:\-]\s*([^\n]{1,100})", text)
    if markers:
        evidence.append("Unfinished task comments: " + "; ".join(f"{m[0]}: {m[1]}" for m in markers[:2]) + ".")
        if action != "cleanup":
            action, reason = "keep", "Keep this file: task comments indicate work to finish, not useless content."

    duplicate_suffix = bool(re.search(r"\(\d+\)$", Path(file_name).stem))
    duplicate_peers = context.get("possible_duplicates", [])
    if duplicate_suffix:
        evidence.append("Numbered download suffix; duplicate contents have not been verified.")
        if duplicate_peers:
            evidence.append("Other similarly named indexed files: " + ", ".join(duplicate_peers[:3]) + ".")
    placeholder = bool(re.fullmatch(r"(?:untitled|new document|document|temp|tmp)(?:[\s_()\-]*\d+)?", Path(file_name).stem, re.I))
    if placeholder and action != "cleanup":
        evidence.append("Generic placeholder filename; readable content is still worth preserving.")
        if not project:
            action, reason = "rename", "Keep the content and give it a descriptive name; a generic filename is not a reason to delete it."
    if profile["content_kind"] == "empty":
        protected_empty = file_name.casefold() in {"__init__.py", ".gitkeep", ".keep", "__init__.pyi"}
        if protected_empty or project:
            action, reason = "keep", "Keep this empty project marker; empty files can have a structural purpose."
        else:
            action, confidence, usefulness, folder = "review", "low", "unknown", ""
            reason = "Empty file; its purpose is unknown. Do not remove it solely because its size is zero."

    if action in {"keep", "cleanup", "review", "rename"}:
        destination = relative
    else:
        destination = f"{folder}/{file_name}"
        if destination.casefold() == relative.casefold():
            action, reason = "keep", "Already in the suggested purpose-specific folder."
    summary = f"{purpose}" + (f" · {topic}" if topic and topic.casefold() not in purpose.casefold() else '')
    ambiguous_topics = len([subject for subject, terms in SUBJECTS if _matches(labels + ' ' + content_signals, terms)]) > 1
    requires_review = (action in {"cleanup", "review", "rename", "archive"}
                       or duplicate_suffix or bool(duplicate_peers) or ambiguous_topics
                       or (profile["content_kind"] == "binary" and folder != 'Installers'))
    action_label = {
        "keep": "Keep with its related project/files",
        "organize": f"Organize into {folder}",
        "cleanup": "Review for safe cleanup (regenerable artifact)",
        "review": "Review manually before deciding",
        "rename": "Keep, then give it a descriptive name",
        "archive": f"Archive in {folder}",
    }.get(action, "Review recommended action")
    return {
        "provider": "local-evidence-v2", "display_title": Path(file_name).stem.replace('_', ' ').replace('-', ' ').title(),
        "description": profile["content_summary"], "tags": sorted(set([ext, topic.casefold()] + [w for w, _ in Counter(re.findall(r'\b[a-zA-Z]{5,}\b', text.casefold())).most_common(3)] ) - {''})[:5],
        "suggested_filename": clean_filename(file_name), **profile,
        "purpose": purpose, "topic": topic, "decision_summary": summary,
        "recommended_action": action, "decision_confidence": confidence,
        "recommended_action_label": action_label,
        "decision_evidence": list(dict.fromkeys(evidence)), "usefulness": usefulness,
        "suggested_folder": folder, "suggested_destination": destination,
        "review_required": requires_review,
        "review_reason": (
            "Review this numbered copy before deleting it; matching contents were not verified."
            if duplicate_suffix else
            'Similarly named files may be duplicates; complete contents have not been compared.'
            if duplicate_peers else
            'Several subjects match this preview; choose the intended subject before moving it.'
            if ambiguous_topics else
            "Review this binary/generated-looking file before deleting it; it may still be needed."
            if profile["content_kind"] == "binary" and action != "cleanup" else
            "Review this generic placeholder filename; keep the content if it has a real purpose."
            if placeholder else
            reason if requires_review else ''
        ),
        "review_reasons": [reason] if requires_review else [], "review_recommendation": reason,
        "possible_duplicate": duplicate_suffix or bool(duplicate_peers),
    }


def analyze_catalog(files: list[dict]) -> list[dict]:
    """Apply sibling/project context in linear time using the indexed catalog."""
    parents = defaultdict(list)
    normalized_names = defaultdict(list)
    projects = set()
    sources = {}
    for row in files:
        relative = str(row["relative_path"]).replace('\\', '/')
        path = PurePosixPath(relative)
        root = row["root_id"]
        parents[(root, str(path.parent))].append(row)
        base_name = re.sub(r"\s*\(\d+\)(?=\.[^.]+$)", '', str(row["name"]).casefold())
        normalized_names[(root, base_name)].append(relative)
        if row["name"].casefold() in PROJECT_MARKERS:
            projects.add((root, str(path.parent)))
        if str(row.get("extension", '')).casefold() in CODE_EXTENSIONS:
            sources[(root, str(path.with_suffix('')).casefold())] = relative
    for (root, parent), rows in parents.items():
        if parent != '.' and any(str(r.get("extension", '')).casefold() in CODE_EXTENSIONS for r in rows):
            projects.add((root, parent))
    results = []
    for row in files:
        relative = str(row["relative_path"]).replace('\\', '/')
        path = PurePosixPath(relative)
        root = row["root_id"]
        project = next((str(parent) for parent in [path.parent, *path.parent.parents]
                        if (root, str(parent)) in projects), None)
        source_key = str(path.with_suffix('')).casefold()
        paired = sources.get((root, source_key))
        base_name = re.sub(r"\s*\(\d+\)(?=\.[^.]+$)", '', str(row["name"]).casefold())
        peers = [r for r in normalized_names[(root, base_name)] if r != relative]
        result = generate_insights(
            row["name"], row.get("extension", ''), relative, row.get("excerpt", ''),
            size_bytes=row.get("size_bytes"), context={"root_label": row.get("root_label", ''),
                                                     "project_folder": project, "paired_source": paired,
                                                     "possible_duplicates": peers},
        )
        results.append({"file_id": row.get("id"), "root_id": root, "name": row["name"],
                        "relative_path": relative, "root_label": row.get("root_label", ''),
                        "root_path": row.get("root_path", ''), "size_bytes": row.get("size_bytes", 0),
                        "modified_at": row.get("modified_at"), **result})
    return results
