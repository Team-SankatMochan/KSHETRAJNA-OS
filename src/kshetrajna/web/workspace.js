/* A focused workspace shell. Existing controls keep their IDs and listeners. */
const viewCopy = {
  overview: ['YOUR DEVICE, UNDERSTOOD', 'Overview', 'A calm view of your workload, pressure and next steps.'],
  chat: ['THINK OUT LOUD', 'Your assistant', 'Conversation and reviewed PC tools, running on your device.'],
  files: ['KNOW WHAT YOU HAVE', 'File library', 'Scan your chosen locations once. Find files and understand your index.'],
  'sort-ai': ['A HOME FOR EVERYTHING', 'Sort AI', 'Connect scattered files to projects and existing folders.'],
  cleanup: ['RECLAIM WASTED DISK SPACE', 'Disk Cleanup', 'Find and safely remove 0-byte files and duplicate copies.'],
  workspaces: ['MAKE ROOM FOR FOCUS', 'Workspaces', 'Your recurring routines and saved application boards.'],
  maintenance: ['A LITTLE HOUSEKEEPING', 'System tools', 'Inspect each action, its scope and its undo limitations.'],
  history: ['EVERY CHANGE, ACCOUNTED FOR', 'History & undo', 'Inspect decisions and restore eligible changes.'],
  privacy: ['YOUR DEVICE. YOUR CHOICE.', 'Settings', 'Control collection, actions, retention and local storage.'],
};
const mainSurface = document.querySelector('main');
const pageSurfaces = {};
for (const key of Object.keys(viewCopy)) {
  const page = document.createElement('div');
  page.className = 'workspace-page';
  page.dataset.page = key;
  pageSurfaces[key] = page;
  mainSurface.insertBefore(page, mainSurface.querySelector('footer'));
}
const originalSections = [...mainSurface.querySelectorAll(':scope > section')];
for (const section of originalSections) {
  let view = 'overview';
  if (section.id === 'chat') view = 'chat';
  else if (section.id === 'maintenance') view = 'maintenance';
  else if (section.id === 'cleanup') view = 'cleanup';
  else if (section.id === 'files' || section.querySelector('#file-insights-types')) view = 'files';
  else if (section.id === 'sort-ai') view = 'sort-ai';
  if (section.querySelector('#workspace-form')) {
    const board = section.querySelector('#workspace-form').closest('article');
    pageSurfaces.workspaces.append(board);
    pageSurfaces.history.append(section);
    continue;
  }
  if (section.querySelector('#privacy')) pageSurfaces.privacy.append($('privacy'));
  pageSurfaces[view].append(section);
}
// One shared source banner remains in Overview; live/demo status stays in the header.
function switchWorkspace() {
  const requested = location.hash.slice(1) || 'overview';
  const aliases = {insights: 'overview', decisions: 'overview', files: 'files', 'sort-ai': 'sort-ai'};
  const key = viewCopy[requested] ? requested : aliases[requested] || 'overview';
  for (const [name, surface] of Object.entries(pageSurfaces)) surface.hidden = name !== key;
  document.querySelectorAll('.side-nav a').forEach(link => {
    const active = link.dataset.view === key;
    link.classList.toggle('selected', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  const [kicker, title, description] = viewCopy[key];
  $('view-kicker').textContent = kicker;
  $('view-title').textContent = title;
  $('view-description').textContent = description;
  document.title = `${title} · Kshetrajna`;
  document.body.dataset.view = key;
  window.scrollTo({top: 0, behavior: 'instant'});
  // Move the virtual cursor to the new page heading after an in-app route
  // change. The initial load keeps the browser's natural focus position.
  if (document.body.dataset.workspaceReady === 'true') {
    requestAnimationFrame(() => $('view-title')?.focus({preventScroll: true}));
  }
  document.body.dataset.workspaceReady = 'true';
}
window.addEventListener('hashchange', switchWorkspace);
switchWorkspace();

let organizationState = {projects: [], folders: [], roots: []};
let destinationSuggestion = null;

function fillRootChoices(select) {
  const current = select.value;
  const roots = (currentFilesState?.roots || []).filter(r => ['ready', 'truncated'].includes(r.status));
  const choices = roots.map(r => [`${r.label} · ${r.path}`, String(r.id)]);
  const signature = JSON.stringify(choices);
  if (select.dataset.choices !== signature) {
    select.replaceChildren(...choices.map(([text, value]) => new Option(text, value)));
    select.dataset.choices = signature;
    if (roots.some(r => String(r.id) === current)) select.value = current;
  }
}

async function refreshOrganization() {
  try {
    organizationState = await request('/api/files/organization');
    fillRootChoices($('project-root'));
    const list = $('project-list');
    list.replaceChildren();
    if (!organizationState.projects.length) list.append(element('p', 'No project scopes yet. Add a name and its preferred folder below, or use a detected group after previewing.', 'empty'));
    for (const project of organizationState.projects) {
      const card = element('div', null, 'project-card');
      const copy = element('div');
      copy.append(element('strong', project.name), element('code', sortFolderPath({path: project.root_path}, project.folder)),
                  element('small', project.aliases.length ? `Also matches: ${project.aliases.join(', ')}` : 'Matches this project name in filenames', 'quiet'));
      card.append(copy, button('Remove rule', async () => {
        if (!await requestSortApproval('Remove this project scope?', `Remove ${project.name}'s matching rule? Existing files stay where they are. Pending previews will expire.`)) return;
        await post('/api/files/projects/remove', {project_id: project.id});
        currentSortPlan = null;
        renderSortPlan(null);
        await refreshOrganization();
      }, 'text-button'));
      list.append(card);
    }
  } catch (error) { $('project-feedback').textContent = error.message; }
}

function renderProjectCandidates(plan) {
  const panel = $('project-candidates');
  panel.replaceChildren();
  const candidates = plan?.report?.project_candidates || [];
  const known = new Set(organizationState.projects.map(p => p.name.toLowerCase()));
  for (const project of candidates.filter(p => !known.has(p.name.toLowerCase())).slice(0, 8)) {
    const card = element('article', null, 'project-candidate');
    card.append(element('span', 'PROJECT GROUP DETECTED', 'eyebrow'), element('h3', project.name),
      element('p', `${project.count} loose files share this name. ${project.destinations.length ? 'Matching existing folders found.' : 'Give them a project folder.'}`),
      element('small', project.examples.join(' · '), 'quiet'));
    const options = project.destinations.length ? project.destinations : [{root_id: project.source_root_id, folder: `Projects/${project.name}`}];
    for (const target of options.slice(0, 3)) {
      card.append(button(`Use ${target.folder}`, () => {
        fillRootChoices($('project-root'));
        $('project-name').value = project.name;
        $('project-aliases').value = '';
        $('project-root').value = String(target.root_id);
        $('project-folder').value = target.folder;
        document.querySelector('.project-settings').open = true;
        $('project-name').focus();
        $('project-feedback').textContent = 'Review this destination and save the scope. Generate a new preview to use it.';
      }, 'secondary'));
    }
    if (project.ambiguous) card.append(element('small', 'Several folders match. Choose the one that owns this project.', 'sort-warning'));
    panel.append(card);
  }
}

$('project-form').addEventListener('submit', async event => {
  event.preventDefault();
  const name = $('project-name').value.trim();
  const root = currentFilesState?.roots.find(r => r.id === Number($('project-root').value));
  const folder = $('project-folder').value.trim().replaceAll('\\', '/');
  if (!root) return;
  if (!await requestSortApproval('Save this project scope?', `Project: ${name}\nDestination: ${sortFolderPath(root, folder)}\n\nFuture previews will match this name and its aliases in filenames. Saving this rule does not move any files; each sorting batch still needs approval.`)) return;
  try {
    await post('/api/files/projects/save', {name, aliases: $('project-aliases').value.split(',').map(a => a.trim()).filter(Boolean), root_id: root.id, folder, confirm: true});
    currentSortPlan = null;
    renderSortPlan(null);
    $('project-feedback').textContent = 'Project saved. Preview again to connect its files to this destination.';
    await refreshOrganization();
  } catch (error) { $('project-feedback').textContent = error.message; }
});

$('scan-laptop').addEventListener('click', async () => {
  try {
    const {locations} = await request('/api/files/locations');
    const list = $('locations-list');
    list.replaceChildren();
    $('locations-all').checked = false;
    $('locations-feedback').textContent = '';
    const normalize = p => p.replaceAll('\\', '/').replace(/\/+$/, '').toLowerCase();
    for (const location of locations) {
      const path = normalize(location.path);
      const coverage = currentFilesState?.roots.find(r => path === normalize(r.path) || path.startsWith(normalize(r.path) + '/'));
      const ancestor = currentFilesState?.roots.find(r => normalize(r.path).startsWith(path + '/'));
      const row = element('label', null, 'location-choice');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.disabled = Boolean(coverage || ancestor);
      input.dataset.location = JSON.stringify(location);
      const copy = element('span');
      copy.append(element('strong', location.label), element('code', location.path));
      if (coverage || ancestor) copy.append(element('small', coverage ? 'Already covered by an approved location' : 'Contains approved folders; add separate non-overlapping children instead', 'quiet'));
      row.append(input, copy);
      list.append(row);
    }
    $('locations-dialog').showModal();
  } catch (error) { $('file-feedback').textContent = error.message; }
});
$('locations-all').addEventListener('change', () => {
  const enabled = [...$('locations-list').querySelectorAll('input:not(:disabled)')];
  enabled.forEach((input, i) => { input.checked = $('locations-all').checked && i < 20 - (currentFilesState?.roots.length || 0); });
});
$('locations-cancel').addEventListener('click', () => $('locations-dialog').close());
$('locations-confirm').addEventListener('click', async () => {
  const locations = [...$('locations-list').querySelectorAll('input:checked:not(:disabled)')].map(input => JSON.parse(input.dataset.location));
  if (!locations.length) { $('locations-feedback').textContent = 'Choose at least one new location.'; return; }
  $('locations-confirm').disabled = true;
  try {
    const result = await post('/api/files/locations/approve', {locations, consent: true});
    $('file-feedback').textContent = `${result.approved.length} locations approved and queued. ${result.skipped.length} skipped. ${result.skipped.map(s => `${s.path}: ${s.reason}`).join(' ')}`;
    $('locations-dialog').close();
    await refreshFiles();
  } catch (error) { $('locations-feedback').textContent = error.message; }
  finally { $('locations-confirm').disabled = false; }
});

function chooseSortDestination(suggestion) {
  destinationSuggestion = suggestion;
  fillRootChoices($('destination-root'));
  $('destination-root').value = String(suggestion.destination_root_id || currentSortPlan.root_id);
  $('destination-folder').value = suggestion.destination_folder || '';
  $('destination-source').textContent = sortFolderPath({path:suggestion.source_root_path}, suggestion.source_relative);
  $('destination-feedback').textContent = '';
  $('sort-destination-dialog').showModal();
}
$('destination-cancel').addEventListener('click', () => $('sort-destination-dialog').close());
$('destination-confirm').addEventListener('click', async () => {
  if (!destinationSuggestion || !currentSortPlan) return;
  const id = destinationSuggestion.id;
  const name = destinationSuggestion.source_relative.split(/[\\/]/).pop();
  const folder = $('destination-folder').value.trim().replaceAll('\\', '/').replace(/\/+$/, '');
  const destination = folder ? `${folder}/${name}` : name;
  try {
    currentSortPlan = await post('/api/files/sort/update', {plan_id:currentSortPlan.id, suggestions:[{id, selected:false,
      destination_relative:destination, destination_root_id:Number($('destination-root').value)}]});
    $('sort-destination-dialog').close();
    renderSortPlan(currentSortPlan);
    await applySortPlan([id]);
  } catch (error) { $('destination-feedback').textContent = error.message; }
});
$('sort-filter').addEventListener('input', () => { sortRenderSignature = ''; renderSortPlan(currentSortPlan); });
document.addEventListener('kshetrajna-files-state', () => {
  const totals = currentFilesState?.totals || currentFilesState?.stats || {};
  const roots = currentFilesState?.roots || [];
  const files = roots.reduce((sum, r) => sum + (r.file_count || 0), 0);
  const list = $('library-summary');
  list.replaceChildren();
  for (const [value, label] of [[files.toLocaleString(), 'indexed files'], [roots.length, 'approved locations'], [roots.filter(r => r.status === 'scanning').length, 'scans in progress']]) {
    const card = element('div'); card.append(element('strong', String(value)), element('small', label)); list.append(card);
  }
  fillRootChoices($('project-root'));
});
refreshOrganization();
