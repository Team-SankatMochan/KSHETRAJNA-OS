/* File Intelligence Dashboard */

let currentFilesState = null;
let currentSortPlan = null;

async function refreshFiles() {
  try {
    const state = await request('/api/files/state');
    renderFiles(state);
    const knownFolders = await request('/api/files/known-folders');
    renderKnownFolders(knownFolders.folders || [], state.roots || []);
    const insights = await request('/api/files/insights');
    renderInsights(insights);
    const actions = await request('/api/files/actions');
    renderActionJournal(actions.actions || []);
    const summaries = state.sort_plans || [];
    const summary = currentSortPlan
      ? summaries.find(plan => plan.id === currentSortPlan.id)
      : summaries.find(plan => ['preview', 'partial', 'applied', 'undo_partial'].includes(plan.status));
    if (summary) {
      try {
        currentSortPlan = await request('/api/files/sort/plan/' + encodeURIComponent(summary.id));
      } catch (error) {
        currentSortPlan = null;
      }
    } else {
      currentSortPlan = null;
    }
    renderSortPlan(currentSortPlan);
  } catch (error) {
    console.error("Failed to fetch file state:", error);
  }
}

function renderKnownFolders(folders, roots) {
  const container = $('known-folders');
  container.replaceChildren();
  if (!folders.length) {
    container.append(element('p', 'No conventional user folders were found. You can enter an approved path below.', 'empty'));
    return;
  }
  const approved = new Set(roots.map(root => root.path.toLowerCase()));
  for (const folder of folders) {
    const row = element('div', null, 'known-folder');
    const details = element('div');
    details.append(element('strong', folder.label), element('small', folder.path, 'root-path'));
    const action = button(approved.has(folder.path.toLowerCase()) ? 'Approved' : 'Approve and scan', async () => {
      await mutateFile('/api/files/roots/add', { path: folder.path, label: folder.label }, `${folder.label} approved and scan started.`);
    }, approved.has(folder.path.toLowerCase()) ? 'text-button' : 'secondary');
    action.disabled = approved.has(folder.path.toLowerCase());
    row.append(details, action);
    container.append(row);
  }
}

function renderFiles(state) {
  currentFilesState = state;
  const demo = state.mode === 'demo';
  $('files-mode').textContent = demo ? 'SYNTHETIC DEMO' : 'LIVE';
  
  const rootsList = $('roots-list');
  rootsList.replaceChildren();
  
  if (state.roots.length === 0) {
    rootsList.append(element('p', 'No watched folders yet.', 'empty'));
  } else {
    for (const root of state.roots) {
      const item = element('div', null, 'root-item');
      
      const info = element('div', null, 'root-info');
      const label = element('strong', root.label || 'Unnamed');
      const status = element('span', root.status.toUpperCase(), 'root-status ' + root.status);
      label.append(status);
      
      const stats = element('small', `${root.file_count} files · ${bytes(root.total_bytes)}`, 'quiet');
      const path = element('span', root.path, 'root-path');
      
      info.append(label, path, stats);
      item.append(info);
      
      const actions = element('div', null, 'control-actions');
      if (root.status === 'scanning') {
        actions.append(button('Cancel', () => mutateFile('/api/files/roots/cancel-scan', {root_id: root.id}, 'Scan cancelled.'), 'secondary'));
      } else {
        actions.append(button('Rescan', () => mutateFile('/api/files/roots/rescan', {root_id: root.id}, 'Rescan started.'), 'secondary'));
      }
      actions.append(button('Remove', () => mutateFile('/api/files/roots/remove', {root_id: root.id}, 'Root removed.'), 'danger text-button'));
      
      item.append(actions);
      rootsList.append(item);
    }
  }

  const sortRoot = $('sort-root');
  const previousRoot = sortRoot.value;
  sortRoot.replaceChildren();
  const readyRoots = state.roots.filter(root => root.status === 'ready');
  if (!readyRoots.length) {
    sortRoot.append(new Option('Add and scan a folder first', ''));
  } else {
    for (const root of readyRoots) {
      sortRoot.append(new Option(`${root.label || 'Approved folder'} · ${root.path}`, String(root.id)));
    }
    sortRoot.value = readyRoots.some(root => String(root.id) === previousRoot) ? previousRoot : String(readyRoots[0].id);
  }
  $('sort-generate').disabled = !readyRoots.length;
}

function renderSortPlan(plan) {
  const meta = $('sort-plan-meta');
  const list = $('sort-suggestions');
  const actions = $('sort-actions');
  const status = $('sort-status');
  meta.replaceChildren();
  list.replaceChildren();
  actions.replaceChildren();
  if (!plan) {
    status.textContent = currentFilesState?.mode === 'demo' ? 'SIMULATION / RULE-BASED' : 'RULE-BASED SUGGESTIONS';
    list.append(element('p', 'Choose a ready approved folder to inspect organization suggestions.', 'empty'));
    return;
  }
  status.textContent = `${plan.mode === 'demo' ? 'SIMULATION / ' : ''}${plan.status.replaceAll('_', ' ').toUpperCase()}`;
  const selectedCount = plan.suggestions.filter(s => s.selected && s.status === 'suggested').length;
  const blockedCount = plan.suggestions.filter(s => s.status === 'blocked').length;
  meta.append(
    element('strong', `${plan.suggestions.length} suggestions · ${selectedCount} selected · ${blockedCount} blocked`),
    element('small', `Preview expires ${timeLabel(plan.expires_at)}. Rule-based evidence is shown per file.`, 'quiet'),
  );
  if (!plan.suggestions.length) {
    list.append(element('p', 'No organization suggestions were found in this indexed folder.', 'empty'));
  }
  for (const suggestion of plan.suggestions) {
    const item = element('article', null, `sort-suggestion ${suggestion.status}`);
    const header = element('div', null, 'sort-suggestion-header');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = suggestion.selected;
    checkbox.disabled = ['applied', 'rolled_back'].includes(suggestion.status);
    checkbox.addEventListener('change', () => { suggestion.selected = checkbox.checked; });
    const title = element('strong', suggestion.source_relative);
    header.append(checkbox, title, element('span', `${suggestion.confidence}/100 evidence`, 'pill'));
    const destination = document.createElement('input');
    destination.className = 'sort-destination';
    destination.value = suggestion.destination_relative;
    destination.maxLength = 240;
    destination.disabled = ['applied', 'rolled_back'].includes(suggestion.status);
    destination.setAttribute('aria-label', `Proposed destination for ${suggestion.source_relative}`);
    destination.addEventListener('input', () => { suggestion.destination_relative = destination.value; });
    item.append(header, element('small', 'Proposed destination', 'eyebrow'), destination,
      element('p', suggestion.reason, 'sort-reason'));
    if (suggestion.error) item.append(element('small', suggestion.error, 'sort-warning'));
    list.append(item);
  }
  const editable = ['preview', 'partial'].includes(plan.status);
  if (editable) {
    actions.append(button('Apply selected changes', applySortPlan, 'primary'));
  }
  if (['applied', 'partial', 'undo_partial'].includes(plan.status)) {
    actions.append(button('Undo eligible changes', undoSortPlan, 'secondary'));
  }
}

async function generateSortPlan() {
  const rootId = Number($('sort-root').value);
  if (!Number.isInteger(rootId) || rootId <= 0) return;
  $('sort-generate').disabled = true;
  try {
    currentSortPlan = await post('/api/files/sort/preview', {
      root_id: rootId,
      create_folders: $('sort-create-folders').checked,
    });
    renderSortPlan(currentSortPlan);
    $('sort-feedback').textContent = 'Suggestions generated. Review each proposed destination.';
  } catch (error) {
    $('sort-feedback').textContent = error.message;
    $('sort-feedback').style.color = '#f0aaa0';
  } finally {
    $('sort-generate').disabled = false;
  }
}

async function applySortPlan() {
  if (!currentSortPlan) return;
  try {
    const suggestions = currentSortPlan.suggestions.map(suggestion => ({
      id: suggestion.id,
      selected: !!suggestion.selected,
      destination_relative: suggestion.destination_relative,
    }));
    currentSortPlan = await post('/api/files/sort/update', {
      plan_id: currentSortPlan.id,
      suggestions,
    });
    const result = await post('/api/files/sort/apply', { plan_id: currentSortPlan.id });
    currentSortPlan = result.plan;
    renderSortPlan(currentSortPlan);
    $('sort-feedback').textContent = `Sort AI finished: ${result.applied.length} applied, ${result.failed.length} failed. Original files were not deleted.`;
    await refreshFiles();
  } catch (error) {
    $('sort-feedback').textContent = error.message;
    $('sort-feedback').style.color = '#f0aaa0';
  }
}

async function undoSortPlan() {
  if (!currentSortPlan) return;
  if (!window.confirm('Undo eligible Sort AI changes? This will not overwrite a path that has changed.')) return;
  try {
    const result = await post('/api/files/sort/undo', { plan_id: currentSortPlan.id });
    currentSortPlan = result.plan;
    renderSortPlan(currentSortPlan);
    $('sort-feedback').textContent = `Sort AI undo: ${result.rolled_back.length} restored, ${result.failed.length} need review.`;
    await refreshFiles();
  } catch (error) {
    $('sort-feedback').textContent = error.message;
    $('sort-feedback').style.color = '#f0aaa0';
  }
}

function renderInsights(insights) {
  const types = $('file-insights-types');
  types.replaceChildren();
  
  if (!insights.type_breakdown || insights.type_breakdown.length === 0) {
    types.append(element('p', 'No insights available yet.', 'empty'));
  } else {
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>Extension</th><th>Count</th><th>Size</th></tr>';
    table.append(thead);
    
    const tbody = document.createElement('tbody');
    for (const type of insights.type_breakdown) {
      const row = tbody.insertRow();
      row.insertCell().textContent = type.extension || '(none)';
      row.insertCell().textContent = type.count;
      row.insertCell().textContent = bytes(type.total_size);
    }
    table.append(tbody);
    types.append(table);
  }
  
  const inbox = $('file-inbox');
  inbox.replaceChildren();
  if (!insights.newest || insights.newest.length === 0) {
    inbox.append(element('p', 'No files cataloged.', 'empty'));
  } else {
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>File</th><th>Size</th><th>Modified</th></tr>';
    table.append(thead);
    
    const tbody = document.createElement('tbody');
    for (const file of insights.newest.slice(0, 10)) {
      const row = tbody.insertRow();
      row.insertCell().textContent = file.name;
      row.insertCell().textContent = bytes(file.size_bytes);
      row.insertCell().textContent = timeLabel(file.modified_at);
    }
    table.append(tbody);
    inbox.append(table);
  }
}

async function mutateFile(path, payload, message) {
  try {
    const result = await post(path, payload);
    setFeedback(message);
    await refreshFiles();
    return result;
  } catch (error) {
    $('file-feedback').textContent = error.message;
    $('file-feedback').style.color = '#f0aaa0';
    return null;
  }
}

$('add-root-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const path = $('root-path').value;
  const label = $('root-label').value;
  await mutateFile('/api/files/roots/add', { path, label }, 'Root added and scan started.');
  $('root-path').value = '';
  $('root-label').value = '';
});

$('delete-files-button').addEventListener('click', async () => {
  if (confirm('Delete all file intelligence data? This will not delete original files.')) {
    await mutateFile('/api/files/delete-all', {}, 'All file data deleted.');
  }
});

$('search-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const query = $('search-query').value;
  try {
    const res = await request('/api/files/search?q=' + encodeURIComponent(query));
    const resultsDiv = $('search-results');
    resultsDiv.replaceChildren();
    
    if (res.results.length === 0) {
      resultsDiv.append(element('p', 'No results found.', 'empty'));
      return;
    }
    
    for (const file of res.results) {
      const item = element('div', null, 'search-result');
      item.append(element('h4', file.name));
      item.append(element('span', file.path || file.relative_path, 'path'));
      
      if (file.excerpt) {
        item.append(element('div', file.excerpt, 'excerpt'));
      }
      const actions = element('div', null, 'file-actions');
      actions.append(
        button('Rename', () => proposeFileAction(file, 'RENAME'), 'text-button'),
        button('Move', () => proposeFileAction(file, 'MOVE'), 'text-button'),
      );
      item.append(actions);
      resultsDiv.append(item);
    }
  } catch (err) {
    setFeedback('Search failed: ' + err.message, true);
  }
});

$('sort-generate').addEventListener('click', generateSortPlan);

async function proposeFileAction(file, kind) {
  const promptText = kind === 'RENAME'
    ? `New filename for ${file.name} (the extension must stay the same):`
    : `New relative path for ${file.name}, including the filename:`;
  const initial = kind === 'RENAME' ? file.name : file.relative_path;
  const destination = window.prompt(promptText, initial);
  if (destination === null) return;
  const trimmed = destination.trim();
  if (!trimmed) {
    setFeedback('A destination is required.', true);
    return;
  }
  try {
    const proposal = await post('/api/files/actions/propose', {
      file_id: file.id,
      kind,
      destination: trimmed,
    });
    const description = `${proposal.source_relative} → ${proposal.destination_relative}`;
    if (!window.confirm(`Review ${kind.toLowerCase()} proposal:\n${description}\n\nApply this file action?`)) {
      await post('/api/files/actions/dismiss', { proposal_id: proposal.proposal_id });
      setFeedback('File action dismissed.');
      await refreshFiles();
      return;
    }
    const result = await post('/api/files/actions/apply', { proposal_id: proposal.proposal_id });
    if (result.status !== 'applied') throw new Error(result.message || 'The file action did not complete.');
    setFeedback(`${kind === 'RENAME' ? 'Rename' : 'Move'} applied and recorded.`);
    await refreshFiles();
  } catch (error) {
    setFeedback(`File action failed: ${error.message}`, true);
    await refreshFiles();
  }
}

function renderActionJournal(actions) {
  const journal = $('file-action-journal');
  journal.replaceChildren();
  if (!actions.length) {
    journal.append(element('p', 'Proposed file changes and their outcomes will appear here.', 'empty'));
    return;
  }
  for (const action of actions.slice(0, 10)) {
    const entry = element('article', null, 'file-action-entry');
    const status = action.status.replaceAll('_', ' ').toUpperCase();
    entry.append(
      element('span', status, 'eyebrow'),
      element('strong', `${action.kind} · ${action.source_relative} → ${action.destination_relative}`),
      element('small', action.error || `Created ${timeLabel(action.created_at)}`),
    );
    const controls = element('div', null, 'control-actions');
    if (action.status === 'proposed') {
      controls.append(
        button('Apply', async () => {
          try {
            const result = await post('/api/files/actions/apply', { proposal_id: action.proposal_id });
            if (result.status !== 'applied') throw new Error(result.message || 'The file action did not complete.');
            setFeedback('File action applied and recorded.');
            await refreshFiles();
          } catch (error) {
            setFeedback(`File action failed: ${error.message}`, true);
            await refreshFiles();
          }
        }, 'primary'),
        button('Dismiss', async () => {
          await mutateFile('/api/files/actions/dismiss', { proposal_id: action.proposal_id }, 'File proposal dismissed.');
        }, 'text-button'),
      );
    } else if (action.status === 'applied') {
      controls.append(button('Undo', async () => {
        try {
          const result = await post('/api/files/actions/undo', { action_id: action.id });
          if (result.status !== 'rolled_back') throw new Error(result.message || 'The file action could not be undone.');
          setFeedback('File action undone when file state allowed it.');
          await refreshFiles();
        } catch (error) {
          setFeedback(`Undo failed: ${error.message}`, true);
          await refreshFiles();
        }
      }, 'secondary'));
    }
    if (controls.childNodes.length) entry.append(controls);
    journal.append(entry);
  }
}

// Initial load
refreshFiles();
setInterval(refreshFiles, 5000);
