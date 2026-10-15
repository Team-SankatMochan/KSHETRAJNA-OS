"""Local project matching and accounting over explicitly approved locations."""
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from .file_scanner import is_sensitive_file, SENSITIVE_DIRS, GENERATED_DIRS
from .sort_ai import build_suggestions, automation_eligible

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


def _root_leaf(root: dict) -> str:
    """Return a platform-neutral leaf for conventional approved roots."""
    value = str(root.get('path') or '').replace('\\', '/').rstrip('/')
    path_leaf = value.rsplit('/', 1)[-1] if value else ''
    label = str(root.get('label') or '').strip()
    # Redirected folders retain the conventional path leaf. Fall back to the
    # label for roots whose path has a non-standard display name.
    return path_leaf.casefold() if path_leaf.casefold() in {'downloads', 'desktop'} else label.casefold()


def organization_plan(files: list[dict], root: dict, projects: list[dict], inventory: list[dict], *,
                      create_folders=False, inspect=True, prefer_existing=True, include_nested=False,
                      route_downloads_to_desktop=True, approved_roots=None, scope_root_ids=None):
    approved_roots = list(approved_roots or [])
    if not any(item.get('id') == root.get('id') for item in approved_roots):
        approved_roots.append(root)
    root_paths = {item['id']: item.get('path', '') for item in approved_roots if item.get('id')}
    root_paths.update({item['root_id']: item['root_path'] for item in inventory
                       if item.get('root_id') and item.get('root_path')})
    root_paths.setdefault(root['id'], root['path'])
    source_is_downloads = _root_leaf(root) == 'downloads'
    desktop_candidates = [item for item in approved_roots
                          if item.get('id') != root.get('id') and item.get('status') in ('ready', 'truncated')
                          and _root_leaf(item) == 'desktop']
    desktop = desktop_candidates[0] if len(desktop_candidates) == 1 else None
    desktop_ambiguous = len(desktop_candidates) > 1
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
            # Keep sensitive files in the review surface so the user sees why
            # they were not automated; build_suggestions marks them blocked.
            candidates.append(row)
        elif any(p == '.' or parent == p or parent.startswith(p + '/') for p in protected):
            reasons['project_structure'] += 1
        elif '/' in relative and not include_nested:
            reasons['existing_collections'] += 1
        else:
            candidates.append(row)
    suggestions = build_suggestions(candidates, Path(root['path']), create_folders=create_folders,
                                    inspect_filesystem=inspect, include_nested=include_nested)
    by_id = {row['id']: row for row in candidates}
    candidate_groups = {candidate['name'].casefold(): candidate
                        for candidate in project_candidates(candidates, inventory)}
    seen = set()
    targets = {}
    for suggestion in suggestions:
        row = by_id[suggestion['file_id']]
        source_relative = row['relative_path'].replace('\\', '/')
        seen.add(row['id'])
        project_matches = ([] if suggestion.get('status') == 'blocked' else
                           [p for p in projects if any(matches(row['name'], a) for a in [p['name'], *p['aliases']])])
        destination = None
        ambiguity = None
        if len(project_matches) == 1:
            project = project_matches[0]
            destination = {'root_id': project['root_id'], 'folder': project['folder'],
                           'root_path': project.get('root_path') or root_paths.get(project['root_id'])}
            suggestion['project_name'] = project['name']
            suggestion['reason'] = f"Project scope {project['name']}: filename matches an approved name/alias. " + suggestion['reason']
        elif len(project_matches) > 1:
            ambiguity = 'Matches multiple project scopes; choose which project owns this file.'
        elif suggestion.get('status') != 'blocked':
            # A repeated project-name prefix is strong evidence even before a
            # project scope is explicitly saved. Reuse a unique existing
            # folder, but leave multiple matches for the user to resolve.
            row_words = words(Path(row['name']).stem)
            group = candidate_groups.get(row_words[0].casefold() if row_words else '')
            if group and group.get('destinations'):
                if len(group['destinations']) == 1 and prefer_existing:
                    destination = group['destinations'][0]
                    suggestion['project_name'] = group['name']
                    suggestion['reason'] = f"Existing project folder {group['name']}: filename group matches the approved folder. " + suggestion['reason']
                elif len(group['destinations']) > 1:
                    ambiguity = 'Filename group matches multiple existing project folders; choose which project owns these files.'

        if (destination is None and suggestion.get('status') != 'blocked'
                and prefer_existing and not suggestion.get('review_required')
                and not (desktop and source_is_downloads)):
            folder = (suggestion['destination_relative'].rsplit('/', 1)[0]
                      if '/' in suggestion['destination_relative'] else '')
            matches_existing = [f for f in inventory if f['name'].casefold() == Path(folder).name.casefold()]
            if len(matches_existing) == 1:
                destination = matches_existing[0]
                suggestion['reason'] = 'Reuses a matching folder in an approved location. ' + suggestion['reason']
            elif len(matches_existing) > 1:
                ambiguity = 'Multiple existing folders match this category; choose a destination.'
        # Downloads are an intake location, not the user's working Desktop.
        # Any ready, explicitly approved Desktop may receive the route even
        # when the current preview contains only Downloads; explicit project
        # scopes and existing project folders win above.
        if (destination is None and suggestion.get('status') != 'blocked'
                and route_downloads_to_desktop and desktop_ambiguous and source_is_downloads
                and '/' not in source_relative and not suggestion.get('review_required')):
            ambiguity = 'Multiple approved Desktop locations are available; choose which Desktop should receive this file.'
        if (destination is None and suggestion.get('status') != 'blocked'
                and route_downloads_to_desktop and desktop and source_is_downloads and '/' not in source_relative
                and not suggestion.get('review_required')):
            folder = (suggestion['destination_relative'].rsplit('/', 1)[0]
                      if '/' in suggestion['destination_relative'] else '')
            desktop_matches = [f for f in inventory
                               if f['root_id'] == desktop['id']
                               and f['folder'].casefold() == folder.casefold()]
            if len(desktop_matches) == 1:
                destination = desktop_matches[0]
            elif len(desktop_matches) == 0:
                destination = {'root_id': desktop['id'], 'root_path': desktop.get('path', ''), 'folder': folder}
            else:
                ambiguity = 'Multiple Desktop folders match this category; choose a destination.'
        target_id = destination['root_id'] if destination else root['id']
        target_root = (destination.get('root_path') if destination else None) or root_paths.get(target_id)
        folder = destination['folder'] if destination else (
            suggestion['destination_relative'].rsplit('/', 1)[0]
            if '/' in suggestion['destination_relative'] else '')
        relative = f"{folder}/{row['name']}" if folder else row['name']
        suggestion.update(destination_root_id=target_id, destination_relative=relative, error=None)
        unchanged = target_id == root['id'] and relative.casefold() == source_relative.casefold()
        if unchanged and not suggestion['review_required'] and not ambiguity:
            reasons['already_organized'] += 1
            suggestion['status'] = 'kept'
            suggestion['selected'] = False
            continue
        blocked = ambiguity or (suggestion.get('blocked_reason') if suggestion.get('status') == 'blocked' else None)
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
        if include_nested and '/' in source_relative:
            suggestion['confidence'] = min(suggestion['confidence'], 87)
            suggestion.update(review_required=True, review_reason='Nested file: confirm moving it outside its current collection.')
        suggestion.update(status='blocked' if blocked else 'suggested', selected=not blocked, error=blocked)
    reasons['already_organized'] += len(candidates) - len(seen)
    suggestions = [s for s in suggestions if s['status'] != 'kept']
    report = {'considered': len(files), 'suggested': len(suggestions),
              'kept': max(0, len(files) - len(suggestions)),
              'advice': sum(not automation_eligible(suggestion) for suggestion in suggestions),
              'reasons': dict(reasons), 'project_candidates': project_candidates(candidates, inventory),
              'prefer_existing': prefer_existing, 'include_nested': include_nested,
              'inventory_folders': len(inventory)}
    return suggestions, report
