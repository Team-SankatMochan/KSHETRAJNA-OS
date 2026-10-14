"""Local project matching and accounting over explicitly approved locations."""
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from .file_scanner import is_sensitive_file, SENSITIVE_DIRS, GENERATED_DIRS
from .sort_ai import build_suggestions

GENERIC = frozenset('documents document pictures picture photos photo images image videos video audio music archives archive downloads download desktop projects project files file folder new untitled screenshot recording notes note report final copy version data spreadsheet presentations presentation installers code setup meeting budget draft scan scanned'.split())
MARKERS = {'package.json', 'pyproject.toml', 'pubspec.yaml', 'cargo.toml', 'go.mod', 'cmakelists.txt', '.git'}
CODE = {'py', 'js', 'jsx', 'ts', 'tsx', 'java', 'rs', 'go', 'dart', 'cpp', 'c', 'h', 'cs', 'kt', 'swift'}


def words(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r'[a-z0-9]+', value.casefold()))


def matches(name: str, alias: str) -> bool:
    # Token boundaries avoid e.g. matching "art" to "aarthika".
    tokens = words(Path(name).stem)
    target = words(alias)
    return bool(target) and any(tokens[i:i + len(target)] == target for i in range(len(tokens)))


def folder_inventory(store, *, inspect=True) -> list[dict]:
    """Use indexed parents plus shallow approved directories, including empty ones."""
    result = {}
    for root in store.get_roots():
        if root['status'] not in ('ready', 'truncated'):
            continue
        base = Path(root['path'])
        def add(relative):
            if not relative or any(p.casefold() in SENSITIVE_DIRS | GENERATED_DIRS for p in relative.split('/')):
                return
            result[(root['id'], relative.casefold())] = {'root_id': root['id'], 'root_path': root['path'],
                                                       'folder': relative, 'name': Path(relative).name}
        with store._connect() as db:
            paths = db.execute('SELECT relative_path FROM files WHERE root_id=?', (root['id'],)).fetchall()
        for row in paths:
            parent = Path(row[0].replace('\\', '/')).parent
            while str(parent) != '.':
                add(parent.as_posix())
                parent = parent.parent
        if inspect:
            queue = [(base, 0)]
            seen = 0
            while queue and seen < 2000:
                directory, depth = queue.pop()
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            seen += 1
                            if seen > 2000:
                                break
                            if entry.name.casefold() in SENSITIVE_DIRS | GENERATED_DIRS or entry.is_symlink():
                                continue
                            info = entry.stat(follow_symlinks=False)
                            if getattr(info, 'st_file_attributes', 0) & 0x400 or not entry.is_dir(follow_symlinks=False):
                                continue
                            path = Path(entry.path)
                            add(path.relative_to(base).as_posix())
                            if depth < 1:
                                queue.append((path, depth + 1))
                except OSError:
                    continue
    return list(result.values())


def project_candidates(files: list[dict], inventory: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in files:
        if '/' in row['relative_path'].replace('\\', '/') or is_sensitive_file(row['name'], row['extension'], row['relative_path']):
            continue
        tokens = words(Path(row['name']).stem)
        if not tokens:
            continue
        prefix = tokens[0]
        if len(prefix) < 4 or prefix in GENERIC or prefix.isdigit():
            continue
        groups[prefix].append(row['name'])
    result = []
    for name, names in groups.items():
        if len(names) < 2:
            continue
        destinations = [f for f in inventory if matches(f['name'], name)]
        result.append({'name': name.title(), 'count': len(names), 'examples': names[:6],
                       'destinations': destinations[:8], 'ambiguous': len(destinations) > 1})
    return sorted(result, key=lambda p: (-p['count'], p['name']))[:30]


def organization_plan(files: list[dict], root: dict, projects: list[dict], inventory: list[dict], *,
                      create_folders=False, inspect=True, prefer_existing=False, include_nested=False):
    protected = set()
    for row in files:
        relative = row['relative_path'].replace('\\', '/')
        if row['name'].casefold() in MARKERS:
            protected.add(str(Path(relative).parent))
    candidates = []
    reasons = Counter()
    for row in files:
        relative = row['relative_path'].replace('\\', '/')
        parent = str(Path(relative).parent)
        if is_sensitive_file(row['name'], row['extension'], relative):
            reasons['sensitive'] += 1
        elif any(p == '.' or parent == p or parent.startswith(p + '/') for p in protected):
            reasons['project_structure'] += 1
        elif '/' in relative and (not include_nested or row['extension'] in CODE):
            reasons['existing_collections'] += 1
        else:
            candidates.append(row)
    suggestions = build_suggestions(candidates, Path(root['path']), create_folders=True, inspect_filesystem=inspect, include_nested=include_nested)
    by_id = {row['id']: row for row in candidates}
    seen = set()
    targets = {}
    for suggestion in suggestions:
        row = by_id[suggestion['file_id']]
        seen.add(row['id'])
        project_matches = [p for p in projects if any(matches(row['name'], a) for a in [p['name'], *p['aliases']])]
        destination = None
        ambiguity = None
        if len(project_matches) == 1:
            project = project_matches[0]
            destination = {'root_id': project['root_id'], 'folder': project['folder']}
            suggestion['project_name'] = project['name']
            suggestion['reason'] = f"Project scope {project['name']}: filename matches an approved name/alias. " + suggestion['reason']
        elif len(project_matches) > 1:
            ambiguity = 'Matches multiple project scopes; choose which project owns this file.'
        elif prefer_existing:
            folder = suggestion['destination_relative'].rsplit('/', 1)[0]
            matches_existing = [f for f in inventory if f['name'].casefold() == Path(folder).name.casefold()]
            if len(matches_existing) == 1:
                destination = matches_existing[0]
                suggestion['reason'] = 'Reuses a matching folder in an approved location. ' + suggestion['reason']
            elif len(matches_existing) > 1:
                ambiguity = 'Multiple existing folders match this category; choose a destination.'
        target_id = destination['root_id'] if destination else root['id']
        target_root = next((f['root_path'] for f in inventory if f['root_id'] == target_id), root['path'] if target_id == root['id'] else None)
        # Explicit projects may target an approved empty root absent from inventory.
        if destination and destination.get('root_path'):
            target_root = destination['root_path']
        folder = destination['folder'] if destination else suggestion['destination_relative'].rsplit('/', 1)[0]
        relative = f"{folder}/{row['name']}"
        suggestion.update(destination_root_id=target_id, destination_relative=relative, error=None)
        unchanged = target_id == root['id'] and relative.casefold() == row['relative_path'].replace('\\', '/').casefold()
        if unchanged and not suggestion['review_required'] and not ambiguity:
            reasons['already_organized'] += 1
            suggestion['status'] = 'kept'
            suggestion['selected'] = False
            continue
        blocked = ambiguity
        if target_root is None:
            blocked = 'Approve and scan the project destination before sorting.'
        elif inspect:
            absolute = Path(target_root) / relative
            if not unchanged and absolute.exists():
                blocked = 'A file already exists at the proposed destination'
            elif not absolute.parent.is_dir() and not create_folders:
                blocked = 'Destination folder does not exist; enable folder creation for this plan'
        key = (target_id, relative.casefold())
        if key in targets:
            blocked = 'Two source files would use the same destination; choose distinct locations.'
            other = targets[key]
            other.update(status='blocked', selected=False, review_required=True, error=blocked)
        targets[key] = suggestion
        if ambiguity:
            suggestion.update(review_required=True, review_reason=ambiguity)
        # Nested moves are explicit opt-in, and project code trees remain protected.
        if include_nested and '/' in row['relative_path'].replace('\\', '/'):
            suggestion['confidence'] = min(suggestion['confidence'], 87)
            suggestion.update(review_required=True, review_reason='Nested file: confirm moving it outside its current collection.')
        suggestion.update(status='blocked' if blocked else 'suggested', selected=not blocked, error=blocked)
    reasons['already_organized'] += len(candidates) - len(seen)
    suggestions = [s for s in suggestions if s['status'] != 'kept']
    report = {'considered': len(files), 'suggested': len(suggestions), 'kept': sum(reasons.values()),
              'reasons': dict(reasons), 'project_candidates': project_candidates(candidates, inventory),
              'prefer_existing': prefer_existing, 'include_nested': include_nested,
              'inventory_folders': len(inventory)}
    return suggestions, report
