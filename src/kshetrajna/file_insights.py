import re
from pathlib import Path

RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
})

def clean_filename(name: str) -> str:
    """Sanitize filename for Windows."""
    # Remove invalid characters
    cleaned = re.sub(r'[<>:"/\\|?*]', '', name)
    # Remove trailing spaces and dots
    cleaned = cleaned.rstrip(' .')
    
    # Check reserved names
    stem = Path(cleaned).stem.upper()
    if stem in RESERVED_NAMES:
        cleaned = f"local_{cleaned}"
        
    return cleaned if cleaned else "unnamed_file"

def generate_insights(file_name: str, extension: str, relative_path: str, excerpt: str) -> dict:
    """Generate deterministic file insights."""
    title = file_name
    if extension and title.endswith(f".{extension}"):
        title = title[:-(len(extension)+1)]
    title = title.replace("-", " ").replace("_", " ").title()

    tags = set()
    if extension:
        tags.add(extension.lower())
    
    path_parts = relative_path.split('/')[:-1]
    for part in path_parts:
        if part:
            tags.add(part.lower())

    desc = f"{extension.upper()} file" if extension else "File"
    if excerpt:
        desc += f" containing {len(excerpt)} characters of text"
        # Extract a few words for tags if possible
        words = [w.lower() for w in re.findall(r'\b[a-zA-Z]{5,}\b', excerpt)]
        from collections import Counter
        common = Counter(words).most_common(3)
        for w, _ in common:
            tags.add(w)

    category = "Document"
    ext = extension.lower()
    if ext in {'py', 'js', 'ts', 'html', 'css', 'json', 'yaml', 'yml', 'toml', 'jsx', 'tsx'}:
        category = "Code"
    elif ext in {'csv', 'tsv', 'xlsx', 'sqlite', 'db'}:
        category = "Data"
    elif ext in {'jpg', 'png', 'svg', 'gif', 'webp', 'mp4', 'mov'}:
        category = "Media"

    return {
        "provider": "local-basic",
        "display_title": title,
        "description": desc,
        "tags": sorted(list(tags))[:5],
        "suggested_filename": clean_filename(file_name),
        "suggested_category": category
    }
