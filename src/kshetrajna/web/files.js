/* File Intelligence Dashboard */

let currentFilesState = null;
let currentSortPlan = null;
let filesRefreshing = false;
let sortBusy = false;
let filesRevision = 0;
let sortRenderSignature = '';
let pendingSortApproval = null;

function setSortScopeControlsDisabled(disabled) {
  for (const id of ['sort-root', 'sort-all-locations', 'prefer-existing', 'route-downloads-to-desktop', 'include-nested']) {
    const control = $(id);
    if (control) control.disabled = disabled;
  }
}

function sortScopeFingerprint() {
  return JSON.stringify({
    root: $('sort-root')?.value || '',
    preferExisting: $('prefer-existing')?.checked === true,
    routeDownloads: $('route-downloads-to-desktop')?.checked !== false,
    includeNested: $('include-nested')?.checked === true,
  });
}

function finishSortApproval(approved) {
  const pending = pendingSortApproval;
  pendingSortApproval = null;
  if ($('sort-confirm-dialog').open) $('sort-confirm-dialog').close();
  if (pending) pending(approved);
}

function requestSortApproval(title, message) {
  const dialog = $('sort-confirm-dialog');
  if (!dialog?.showModal) return Promise.resolve(window.confirm(message));
  if (pendingSortApproval) finishSortApproval(false);
  $('sort-confirm-title').textContent = title;
  $('sort-confirm-copy').textContent = message;
  dialog.showModal();
  return new Promise(resolve => {pendingSortApproval = resolve;});
}
let pendingRootApproval = null;
let pendingFileReview = null;
let filePreviewRequest = 0;
let filePreviewController = null;
let filePreviewImageUrl = null;
let filePreviewPdfUrl = null;

function finishRootApproval(approved) {
  const pending = pendingRootApproval;
  pendingRootApproval = null;
  const dialog = $('file-permission-dialog');
  if (dialog?.open) dialog.close();
  if (pending) pending.resolve(approved);
}

function requestRootApproval(path, label) {
  const dialog = $('file-permission-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') {
    return Promise.resolve(window.confirm(
      `Approve Kshetrajna to inspect ${label || 'this folder'}?\n\n` +
      `It reads bounded local metadata and allowed text excerpts only; it does not move or delete files while scanning.\n\n${path}`
    ));
  }
  if (pendingRootApproval) finishRootApproval(false);
  $('file-permission-label').textContent = label || 'Selected folder';
  $('file-permission-path').textContent = path;
  $('file-permission-copy').textContent = `Before indexing ${label || 'this folder'}, choose whether Kshetrajna may inspect it locally.`;
  dialog.showModal();
  return new Promise(resolve => { pendingRootApproval = { resolve }; });
}

function sortRoot() {
  if ($('sort-root').value === 'all') return {id: 'all', path: 'All approved locations'};
  const rootId = Number($('sort-root').value);
  return currentFilesState?.roots?.find(root => root.id === rootId) || null;
}

function sortFolderPath(root, folder) {
  const separator = root.path.includes('\\') ? '\\' : '/';
  return root.path.replace(/[\\/]+$/, '') + separator + folder.split('/').join(separator);
}

function sortLocation(root) {
  const parts = root.path.split(/[\\/]/).filter(Boolean);
  const leaf = parts[parts.length - 1] || '';
  return leaf.toLowerCase() === 'desktop' ? `your Desktop (${root.path})` : root.path;
}

function sortGroups(plan) {
  const groups = new Map();
  for (const suggestion of plan.suggestions) {
    const folder = suggestion.destination_folder || suggestion.destination_relative.split(/[\\/]/).slice(0, -1).join('/');
    const key = `${suggestion.destination_root_id || plan.root_id}:${folder}`;
    if (!groups.has(key)) groups.set(key, {
      folder,
      rootPath: suggestion.destination_root_path || plan.root_path,
      rootLabel: suggestion.destination_root_label || '',
      items: [],
    });
    groups.get(key).items.push(suggestion);
  }
  return [...groups.values()];
}

function rootLeaf(path) {
  return String(path || '').split(/[\\/]/).filter(Boolean).pop()?.toLowerCase() || '';
}

function rootDisplayName(root, fallback = 'approved location') {
  return root?.label || (rootLeaf(root?.path) ? rootLeaf(root.path).replace(/^./, letter => letter.toUpperCase()) : fallback);
}

function isRootNamed(root, name) {
  return rootLeaf(root?.path) === name || String(root?.label || '').trim().toLowerCase() === name;
}

function renderSortScopeSummary(state) {
  const summary = $('sort-scope-summary');
  const stats = $('sort-scope-stats');
  if (!summary || !stats) return;
  stats.replaceChildren();
  const roots = state?.roots || [];
  const indexed = roots.reduce((total, root) => total + Number(root.file_count || 0), 0);
  const scanning = roots.filter(root => root.status === 'scanning');
  const partial = roots.filter(root => root.status === 'truncated');
  const errors = roots.filter(root => root.status === 'error');
  const cancelled = roots.filter(root => root.status === 'cancelled');
  let title = 'No approved locations yet';
  let detail = 'Approve and scan a location in File library before creating a sorting preview.';
  let stateClass = 'empty';
  if (roots.length) {
    if (scanning.length) {
      title = 'Scope is still indexing';
      detail = `${scanning.length} location${scanning.length === 1 ? '' : 's'} still scanning. Preview unlocks when the selected locations are ready.`;
      stateClass = 'scanning';
    } else if (errors.length) {
      title = 'Scope needs attention';
      detail = `${errors.length} approved location${errors.length === 1 ? '' : 's'} could not finish scanning. Review the File library before sorting.`;
      stateClass = 'error';
    } else if (cancelled.length) {
      title = 'A scan was cancelled';
      detail = `${cancelled.length} location${cancelled.length === 1 ? '' : 's'} need a rescan before Sort AI can use them.`;
      stateClass = 'cancelled';
    } else if (partial.length) {
      title = 'Ready with partial coverage';
      detail = 'Sort AI can work on the indexed subset. A scan limit was reached in one or more approved locations.';
      stateClass = 'partial';
    } else {
      title = 'Scope ready for a preview';
      detail = 'Indexed files are available. Choose one location or compare all approved locations together.';
      stateClass = 'ready';
    }
  }
  summary.dataset.state = stateClass;
  const status = summary.querySelector('.sort-scope-status');
  status.replaceChildren(
    element('span', null, `sort-status-dot ${stateClass}`),
    (() => {
      const copy = element('div');
      copy.append(element('strong', title), element('p', detail));
      return copy;
    })(),
  );
  const scanLabel = scanning.length ? `${scanning.length} scanning` : partial.length ? `${partial.length} partial` : errors.length ? `${errors.length} attention` : cancelled.length ? `${cancelled.length} cancelled` : roots.length ? 'Ready' : 'Not started';
  for (const [value, label, className] of [
    [indexed.toLocaleString(), 'indexed files', 'indexed'],
    [roots.length.toLocaleString(), 'approved locations', 'locations'],
    [scanLabel, 'scan status', stateClass],
  ]) {
    const card = element('div', null, `sort-scope-stat ${className}`);
    card.append(element('strong', String(value)), element('small', label));
    stats.append(card);
  }
  renderDownloadsHint(state);
}

function renderDownloadsHint(state, plan = null) {
  const hint = $('sort-routing-hint');
  if (!hint) return;
  const roots = state?.roots || [];
  const eligibleRoots = roots.filter(root => ['ready', 'truncated'].includes(root.status));
  const selected = $('sort-root')?.value;
  const scopeRoots = selected === 'all' ? eligibleRoots : eligibleRoots.filter(root => String(root.id) === String(selected));
  const downloads = scopeRoots.find(root => isRootNamed(root, 'downloads'));
  if (!downloads) {
    hint.hidden = true;
    hint.replaceChildren();
    return;
  }
  const desktop = eligibleRoots.find(root => isRootNamed(root, 'desktop'));
  const routeEnabled = $('route-downloads-to-desktop')?.checked !== false;
  const routedCount = plan?.suggestions?.filter(suggestion =>
    isRootNamed({path: suggestion.source_root_path}, 'downloads') &&
    isRootNamed({path: suggestion.destination_root_path}, 'desktop')).length || 0;
  hint.hidden = false;
  hint.dataset.available = desktop ? 'true' : 'false';
  const copy = element('div');
  if (desktop && routeEnabled) {
    copy.append(
      element('strong', routedCount ? `Downloads → ${rootDisplayName(desktop)}` : 'Downloads can use your approved Desktop'),
      element('span', routedCount
        ? `${routedCount} clear match${routedCount === 1 ? '' : 'es'} in this preview point to ${desktop.path}. Review the destination before approval.`
        : `Clear loose Downloads matches can be routed to ${desktop.path}, even when you preview only Downloads. Ambiguous or sensitive files stay in review.`),
    );
  } else if (desktop) {
    copy.append(
      element('strong', 'Downloads and Desktop are both approved'),
      element('span', 'Desktop routing preference is off. Review the preview paths; the backend still chooses a safe destination.'),
    );
  } else {
    copy.append(
      element('strong', 'Downloads is approved, but Desktop is not in scope'),
      element('span', 'Approve Desktop and include it in this scope if you want clear Downloads matches to use it.'),
    );
  }
  hint.replaceChildren(element('span', 'ROUTING NOTE', 'eyebrow'), copy);
}

async function refreshFiles() {
  if (filesRefreshing || sortBusy || appStopped) return;
  filesRefreshing = true;
  const revision = filesRevision;
  try {
    const [state, knownFolders, insights, actions] = await Promise.all([
      request('/api/files/state'), request('/api/files/known-folders'),
      request('/api/files/insights'), request('/api/files/actions'),
    ]);
    if (sortBusy || revision !== filesRevision) return;
    renderFiles(state);
    renderKnownFolders(knownFolders.folders || [], state.roots || []);
    renderInsights(insights);
    renderActionJournal(actions.actions || []);
    const summaries = state.sort_plans || [];
    const selectedSortValue = $('sort-root').value;
    const selectedReadyIds = state.roots.filter(root => ['ready', 'truncated'].includes(root.status)).map(root => root.id).sort((a, b) => a - b);
    const summaryMatchesScope = plan => {
      try {
        const report = typeof plan.report === 'string' ? JSON.parse(plan.report || '{}') : plan.report || {};
        const planRoots = (report.source_roots || [plan.root_id]).map(Number).sort((a, b) => a - b);
        if (selectedSortValue !== 'all') {
          return planRoots.length === 1 && planRoots[0] === Number(selectedSortValue);
        }
        return JSON.stringify(planRoots) === JSON.stringify(selectedReadyIds);
      } catch (error) { return false; }
    };
    let summary = currentSortPlan
      ? summaries.find(plan => plan.id === currentSortPlan.id)
      : summaries.find(plan => summaryMatchesScope(plan) && ['preview', 'partial', 'applied', 'undo_partial'].includes(plan.status));
    if (summary && Date.parse(summary.expires_at) <= Date.now()) {
      summary = summaries.find(p => p.root_id === summary.root_id && p.status === 'preview' && Date.parse(p.expires_at) > Date.now()) || summary;
    }
    if (summary) {
      try {
        const plan = await request('/api/files/sort/plan/' + encodeURIComponent(summary.id));
        if (sortBusy || revision !== filesRevision) return;
        currentSortPlan = plan;
      } catch (error) {
        currentSortPlan = null;
      }
    } else {
      currentSortPlan = null;
    }
    renderSortPlan(currentSortPlan);
  } catch (error) {
    $('file-feedback').textContent = `Could not refresh files: ${error.message}. Check that the local app is running.`;
    const summary = $('sort-scope-summary');
    const status = summary?.querySelector('.sort-scope-status');
    if (status) {
      summary.dataset.state = 'error';
      status.replaceChildren(element('span', null, 'sort-status-dot error'), (() => {
        const copy = element('div');
        copy.append(element('strong', 'Scope status unavailable'), element('p', 'The local file service did not respond. Try again when the app is running.'));
        return copy;
      })());
    }
  } finally {
    filesRefreshing = false;
  }
}

function renderKnownFolders(folders, roots) {
  const container = $('known-folders');
  container.replaceChildren();
  if (!folders.length) {
    container.append(element('p', 'No conventional user folders were found. You can enter an approved path below.', 'empty'));
    return;
  }
  for (const folder of folders) {
    const normalize = path => path.replace(/[\\/]+$/, '').replaceAll('\\', '/').toLowerCase();
    const match = roots.find(root => normalize(root.path) === normalize(folder.path));
    const covered = roots.find(root => normalize(folder.path).startsWith(normalize(root.path) + '/'));
    const row = element('div', null, 'known-folder');
    const details = element('div');
    details.append(element('strong', folder.label), element('small', folder.path, 'root-path'));
    const action = button(covered ? 'Covered by approved folder' : match ? 'Rescan folder' : 'Approve and scan', async () => {
      if (match) {
        await mutateFile('/api/files/roots/rescan', {root_id: match.id}, `${folder.label} rescan started.`);
        return;
      }
      if (await requestRootApproval(folder.path, folder.label)) {
        await mutateFile('/api/files/roots/add', { path: folder.path, label: folder.label, consent: true }, `${folder.label} approved and scan started.`);
      }
    }, 'secondary');
    action.disabled = Boolean(covered || match?.status === 'scanning');
    row.append(details, action);
    container.append(row);
  }
}

function renderFiles(state) {
  currentFilesState = state;
  const demo = state.mode === 'demo';
  $('files-mode').textContent = demo ? 'SYNTHETIC DEMO' : 'LIVE';
  renderSortScopeSummary(state);
  
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
      
      const stats = element('small', root.status === 'scanning' ? `Scanning… ${root.scan?.files_seen || 0} files indexed so far` : `${root.file_count} files · ${bytes(root.total_bytes)}`, 'quiet');
      const path = element('span', root.path, 'root-path');
      
      info.append(label, path, stats);
      if (root.error) info.append(element('small', root.error, 'sort-warning'));
      if (root.watch_enabled) info.append(element('small', `Watching · ${root.watch_message || 'Waiting for new downloads'}`, 'watch-state'));
      if (root.status === 'truncated') info.append(element('small', 'Scan limit reached. You can sort the indexed subset or approve a smaller folder.', 'sort-warning'));
      if (root.status === 'ready' && !root.file_count) info.append(element('small', 'No indexable files found. Cloud-only files and links are skipped; make cloud files available offline before rescanning.', 'quiet'));
      item.append(info);
      
      const actions = element('div', null, 'control-actions');
      if (root.status === 'scanning') {
        actions.append(button('Cancel', () => mutateFile('/api/files/roots/cancel-scan', {root_id: root.id}, 'Scan cancelled.'), 'secondary'));
      } else {
        actions.append(button('Rescan', () => mutateFile('/api/files/roots/rescan', {root_id: root.id}, 'Rescan started.'), 'secondary'));
      }
      actions.append(button('Remove', () => mutateFile('/api/files/roots/remove', {root_id: root.id}, 'Root removed.'), 'danger text-button'));
      if (root.watch_enabled && root.watch_message?.includes('New arrivals checked:')) actions.append(button('Review new arrivals', async () => {
        $('sort-root').value = String(root.id);
        if ($('sort-all-locations')) $('sort-all-locations').checked = false;
        location.hash = 'sort-ai';
        await generateSortPlan();
      }, 'primary'));
      if (!demo) actions.append(button(root.watch_enabled ? 'Pause watching' : 'Watch new files', async () => {
        if (!root.watch_enabled && !await requestSortApproval('Watch this approved folder?', `${root.path}\n\nCheck immediate files every 2 seconds, wait for stable size and timestamp, then refresh the index. Partial downloads are ignored. No file moves automatically; review a fresh sorting batch when ready. Watching stops when the app quits.`)) return;
        await mutateFile('/api/files/roots/watch', {root_id:root.id, enabled:!root.watch_enabled}, root.watch_enabled ? 'Folder watching paused.' : 'Watching new downloads.');
      }, 'secondary'));
      
      item.append(actions);
      rootsList.append(item);
    }
  }

  const sortRoot = $('sort-root');
  const previousRoot = sortRoot.value;
  const readyRoots = state.roots.filter(root => ['ready', 'truncated'].includes(root.status));
  const scansPending = state.roots.some(root => root.status === 'scanning');
  const options = readyRoots.length ? [...(readyRoots.length > 1 && !scansPending ? [['All approved locations', 'all']] : []), ...readyRoots.map(root => [`${root.label || 'Approved folder'} · ${root.path}`, String(root.id)])] : [['Add and scan a folder first', '']];
  const optionSignature = JSON.stringify(options);
  if (sortRoot.dataset.options !== optionSignature) {
    sortRoot.replaceChildren();
    for (const [label, value] of options) {
      sortRoot.append(new Option(label, value));
    }
    sortRoot.dataset.options = optionSignature;
    sortRoot.value = options.some(([,value]) => value === previousRoot) ? previousRoot : options[0][1];
  }
  const allLocations = $('sort-all-locations');
  if (allLocations) {
    allLocations.disabled = readyRoots.length < 2 || scansPending || sortBusy;
    allLocations.checked = sortRoot.value === 'all';
    allLocations.closest('.sort-all-toggle')?.classList.toggle('selected', allLocations.checked);
    const note = allLocations.closest('.sort-all-toggle')?.querySelector('small');
    if (note) note.textContent = scansPending ? 'Available after approved scans finish.' : 'Compare sources and destinations across this scope.';
  }
  renderDownloadsHint(state);
  $('sort-generate').disabled = !readyRoots.length || sortBusy;
  $('sort-auto').disabled = !readyRoots.length || sortBusy;
  const history = $('sort-history');
  const batches = (state.sort_plans || []).filter(plan => ['applied','partial','undo_partial','rolled_back'].includes(plan.status));
  const choices = [['Recent batches: select to inspect or undo', ''], ...batches.map(plan => {
    const root = state.roots.find(root => root.id === plan.root_id);
    return [`${root?.label || 'Approved folder'} / ${timeLabel(plan.created_at)} / ${plan.status.replaceAll('_',' ')}`, plan.id];
  })];
  const historySignature = JSON.stringify(choices);
  if (history.dataset.options !== historySignature) {
    const selected = history.value;
    history.replaceChildren(...choices.map(([label, value]) => new Option(label, value)));
    history.dataset.options = historySignature;
    history.value = batches.some(plan => plan.id === selected) ? selected : '';
  }
  document.dispatchEvent(new CustomEvent('kshetrajna-files-state', {detail:state}));
}

function renderSortPlan(plan) {
  const signature = JSON.stringify([plan, $('sort-root').value, currentFilesState?.mode, $('sort-filter')?.value]);
  if (signature === sortRenderSignature) return;
  sortRenderSignature = signature;
  const meta = $('sort-plan-meta');
  const list = $('sort-suggestions');
  const actions = $('sort-actions');
  const status = $('sort-status');
  meta.replaceChildren();
  list.replaceChildren();
  actions.replaceChildren();
  if (typeof renderProjectCandidates === 'function') renderProjectCandidates(plan);
  if (!plan) {
    status.textContent = currentFilesState?.mode === 'demo' ? 'SIMULATION / READY' : 'READY FOR A PREVIEW';
    $('sort-filter-wrap').hidden = true;
    renderDownloadsHint(currentFilesState);
    const empty = element('div', null, 'sort-empty-state');
    if (currentFilesState?.roots?.length) {
      empty.append(element('strong', 'Choose a ready scope to begin.'), element('p', 'Preview the indexed files first. Sort AI will put clear matches above the advice queue.'));
    } else {
      empty.append(element('strong', 'No approved files to organize yet.'), element('p', 'Approve and scan a location in File library, then return here to preview safe matches.'));
      const link = element('a', 'Open File library ↗', 'sort-empty-link');
      link.href = '#files';
      empty.append(link);
    }
    list.append(empty);
    return;
  }
  status.textContent = `${plan.mode === 'demo' ? 'SIMULATION / ' : ''}${plan.status.replaceAll('_', ' ').toUpperCase()}`;
  const automatic = plan.suggestions.filter(s => s.automation_eligible && s.status === 'suggested');
  const needsReview = plan.suggestions.filter(s => !s.automation_eligible && ['suggested', 'blocked', 'failed', 'undo_failed'].includes(s.status));
  const completed = plan.suggestions.filter(s => ['applied', 'rolled_back'].includes(s.status));
  const openPlan = ['preview', 'partial'].includes(plan.status);
  const automaticCount = openPlan && Number.isFinite(Number(plan.automatic_count)) ? Number(plan.automatic_count) : automatic.length;
  const reviewCount = Number.isFinite(Number(plan.review_count)) ? Number(plan.review_count) : needsReview.length;
  const filter = $('sort-filter')?.value.trim().toLowerCase() || '';
  const filtered = needsReview.filter(s => !filter || [s.source_relative,s.destination_relative,s.project_name,s.reason].join(' ').toLowerCase().includes(filter));
  const groups = sortGroups({...plan, suggestions: filtered.slice(0, 100)});
  const report = plan.report || {};
  const counters = element('div', null, 'summary-grid');
  for (const [count, label, className] of [[report.considered ?? plan.suggestions.length, 'files considered', 'considered'],[automaticCount, 'clear matches', 'automatic'],[reviewCount, 'need your advice', 'review'],[report.kept || 0, 'kept in place', 'kept'],[report.remembered_keep || 0, 'remembered keeps', 'remembered'],[completed.filter(s => s.status === 'applied').length, 'moved this batch', 'moved']]) {
    const cell = element('div', null, `sort-plan-stat ${className}`);
    cell.append(element('strong', Number(count).toLocaleString()), element('small', label)); counters.append(cell);
  }
  meta.append(counters);
  meta.append(
    element('strong', `${automaticCount} clear matches ready · ${reviewCount} need your advice · ${completed.filter(s => s.status === 'applied').length} moved`),
    element('small', `Auto-sort moves up to ${plan.batch_limit || 500} clear matches per batch. Preview expires ${timeLabel(plan.expires_at)}.`, 'quiet'),
  );
  if (report.reasons) {
    const labels = {already_organized:'already organized', existing_collections:'existing collections', project_structure:'project structure preserved', sensitive:'sensitive files excluded'};
    meta.append(element('small', Object.entries(report.reasons).map(([key,count]) => `${Number(count).toLocaleString()} ${labels[key] || key}`).join(' · '), 'quiet'));
  }
  if (plan.scan_partial) {
    const partialNames = (plan.partial_source_roots || []).map(root => root.label || root.path).join(', ');
    meta.append(element('small', partialNames
      ? `Partial scan in ${partialNames}: only the files indexed in the latest pass are considered.`
      : 'Partial scan: only the files indexed in the latest pass are considered.', 'sort-warning'));
  }
  if (plan.error) meta.append(element('p', plan.error, 'sort-warning'));
  renderDownloadsHint(currentFilesState, plan);
  $('sort-filter-wrap').hidden = !needsReview.length;
  if (automatic.length) {
    const section = element('section', null, 'sort-auto-queue');
    const heading = element('div', null, 'sort-queue-heading');
    heading.append(element('div', null, 'sort-queue-icon'), (() => {
      const copy = element('div');
      copy.append(element('strong', `Ready to auto-sort · ${automaticCount} clear match${automaticCount === 1 ? '' : 'es'}`), element('small', 'Destinations are explainable and can be undone from the journal.', 'quiet'));
      return copy;
    })());
    const details = document.createElement('details');
    details.className = 'sort-auto-summary';
    details.open = true;
    details.append(element('summary', 'Inspect destination groups before approving'));
    for (const group of sortGroups({...plan, suggestions: automatic})) {
      const destination = element('div', null, 'sort-auto-destination');
      destination.append(element('strong', `${group.items.length} file${group.items.length === 1 ? '' : 's'}`), element('code', group.folder ? sortFolderPath({path:group.rootPath}, group.folder) : group.rootPath));
      details.append(destination);
      details.append(element('small', group.items.slice(0, 10).map(s => s.source_relative).join(', ') + (group.items.length > 10 ? '…' : ''), 'quiet'));
    }
    section.append(heading, details);
    list.append(section);
  }
  if (needsReview.length) {
    const reviewHeading = element('div', null, 'sort-review-queue-heading');
    reviewHeading.append(element('div', null, 'sort-review-icon'), (() => {
      const copy = element('div');
      copy.append(element('strong', `Advice queue · ${reviewCount} file${reviewCount === 1 ? '' : 's'}`), element('small', 'These files are ambiguous, sensitive, blocked, or otherwise need your decision.', 'quiet'));
      return copy;
    })());
    list.append(reviewHeading);
  }
  if (!needsReview.length && plan.suggestions.length) list.append(element('p', automatic.length ? 'No ambiguous files need your advice. Inspect the destinations above, then use Auto-sort clear matches.' : 'No pending sorting decisions. Kept files stay where they are; undo is available for eligible moves.', 'empty'));
  if (filtered.length > 100) list.append(element('p', `Showing 100 of ${filtered.length} matching decisions. Narrow the filter to find another file. Remaining files stay untouched.`, 'quiet'));
  if (!plan.suggestions.length) {
    const empty = element('div', null, 'sort-empty-state');
    empty.append(element('strong', 'No safe organization ideas found in this preview.'), element('p', 'Files stay where they are. Try another approved location or adjust project scopes if the files belong together.'));
    list.append(empty);
  }
  for (const group of groups) {
    const movable = group.items.filter(suggestion => suggestion.status === 'suggested');
    const blocked = group.items.filter(suggestion => suggestion.status === 'blocked');
    const card = element('article', null, `sort-conversation${blocked.length && !movable.length ? ' blocked' : ''}`);
    const noun = group.items.length === 1 ? 'this file' : 'these files';
    const destination = group.folder ? sortFolderPath({path:group.rootPath}, group.folder) : group.rootPath;
    const cardHeading = element('div', null, 'sort-card-heading');
    const headingCopy = element('div');
    headingCopy.append(element('p', group.folder ? `Suggested home for ${noun}` : `Review ${noun} before choosing a home.`), element('small', group.folder ? `Matched folder: ${group.folder}` : 'No safe folder match was selected.', 'quiet'));
    const destinationBlock = element('div', null, 'sort-destination-block');
    destinationBlock.append(element('span', `TO ${group.rootLabel || rootDisplayName({path: group.rootPath})}`, 'sort-path-label'), element('code', destination));
    cardHeading.append(headingCopy, destinationBlock);
    card.append(cardHeading);
    const summaries = [...new Set(group.items.map(suggestion => suggestion.content_summary).filter(Boolean))];
    if (summaries.length) {
      card.append(element('small', `What I saw in the approved preview: ${summaries.join(' ')}`, 'sort-content-summary sort-content-summary-banner'));
    }
    const fileList = document.createElement('ul');
    fileList.className = 'sort-conversation-files';
    for (const suggestion of group.items) {
      const item = document.createElement('li');
      const sourcePath = sortFolderPath({path:suggestion.source_root_path || plan.root_path}, suggestion.source_relative);
      const sourceBlock = element('div', null, 'sort-source-block');
      sourceBlock.append(element('span', `FROM ${rootDisplayName({path: suggestion.source_root_path || plan.root_path})}`, 'sort-path-label'), element('span', suggestion.source_relative, 'sort-source-name'), element('code', sourcePath, 'sort-source-path'));
      item.append(sourceBlock);
      if (suggestion.content_summary) {
        item.append(element('small', suggestion.content_summary, 'sort-content-summary'));
      }
      if (suggestion.review_required && suggestion.review_reason) {
        item.append(element('small', suggestion.review_reason, 'sort-review-note'));
      }
      if (suggestion.file_id) {
        item.append(button('Preview & decide', () => openFileReview(suggestion.file_id), 'text-button'));
        if (['preview','partial'].includes(plan.status)) item.append(button('Choose destination', () => chooseSortDestination(suggestion), 'secondary'));
      }
      fileList.append(item);
    }
    card.append(fileList);
    const reviews = group.items.filter(suggestion => suggestion.review_required && suggestion.review_reason);
    if (reviews.length) {
      card.append(element('div', `Please review ${reviews.length === 1 ? 'this file' : 'these files'} before moving them. ${reviews.map(suggestion => suggestion.review_reason).join(' ')}`, 'sort-review-banner'));
    }
    if (blocked.length) {
      card.append(element('small', blocked.map(suggestion => suggestion.error || 'This file needs review.').join(' '), 'sort-warning'));
    }
    if (movable.length && ['preview', 'partial'].includes(plan.status)) {
      const actionLabel = `Move ${movable.length === 1 ? 'this reviewed file' : 'these reviewed files'}`;
      card.append(button(actionLabel, () => applySortPlan(movable.map(suggestion => suggestion.id)), 'primary'));
    }
    const pending = group.items.filter(s => ['suggested', 'blocked', 'failed'].includes(s.status));
    if (pending.length) card.append(button(pending.length === 1 ? 'Keep this here' : 'Keep these here', () => keepSortFiles(pending.map(s => s.id)), 'secondary'));
    list.append(card);
  }
  if (['applied', 'partial', 'undo_partial'].includes(plan.status)) {
    actions.append(button('Undo eligible changes', undoSortPlan, 'secondary'));
  }
}

async function generateSortPlan() {
  if (sortBusy) return;
  const all = $('sort-root').value === 'all';
  const rootId = Number($('sort-root').value);
  if (!all && (!Number.isInteger(rootId) || rootId <= 0)) return;
  sortBusy = true;
  filesRevision += 1;
  const requestRevision = filesRevision;
  const requestScope = sortScopeFingerprint();
  currentSortPlan = null;
  setSortScopeControlsDisabled(true);
  $('sort-generate').disabled = true;
  $('sort-auto').disabled = true;
  $('sort-all-locations').disabled = true;
  $('sort-status').textContent = 'BUILDING PREVIEW';
  $('sort-scope-summary').setAttribute('aria-busy', 'true');
  $('sort-plan-meta').replaceChildren();
  $('sort-suggestions').replaceChildren(element('div', null, 'sort-loading-state'));
  $('sort-suggestions').firstElementChild.append(element('strong', 'Reading your approved scope…'), element('p', 'Matching filenames, existing folders, and safe destinations locally.'));
  $('sort-feedback').textContent = '';
  $('sort-feedback').style.color = '';
  try {
    const sortPayload = {
      ...(all ? {root_ids:currentFilesState.roots.filter(r => ['ready','truncated'].includes(r.status)).map(r => r.id)} : {root_id:rootId}),
      create_folders: true,
      prefer_existing: $('prefer-existing').checked,
      include_nested: $('include-nested').checked,
      // Older servers ignore unknown JSON keys and keep their safe Downloads default;
      // newer servers can honor this preference without changing the request shape.
      route_downloads_to_desktop: $('route-downloads-to-desktop')?.checked !== false,
    };
    const preview = await post(all ? '/api/files/sort/scope-preview' : '/api/files/sort/preview', sortPayload);
    if (requestRevision !== filesRevision || requestScope !== sortScopeFingerprint()) {
      $('sort-feedback').textContent = 'The scope changed while the preview was loading. Preview again to use the current selection.';
      return;
    }
    currentSortPlan = preview;
    renderSortPlan(currentSortPlan);
    $('sort-feedback').textContent = `${currentSortPlan.automatic_count} clear matches can be sorted together; ${currentSortPlan.review_count} files need your advice.`;
    $('sort-feedback').style.color = '';
  } catch (error) {
    $('sort-status').textContent = 'PREVIEW UNAVAILABLE';
    $('sort-suggestions').replaceChildren(element('div', null, 'sort-empty-state error'));
    $('sort-suggestions').firstElementChild.append(element('strong', 'The preview could not be created.'), element('p', error.message));
    $('sort-feedback').textContent = error.message;
    $('sort-feedback').style.color = '#f0aaa0';
  } finally {
    sortBusy = false;
    $('sort-scope-summary').setAttribute('aria-busy', 'false');
    $('sort-generate').disabled = false;
    $('sort-auto').disabled = false;
    setSortScopeControlsDisabled(false);
    const readyCount = currentFilesState?.roots?.filter(root => ['ready', 'truncated'].includes(root.status)).length || 0;
    const scansPending = currentFilesState?.roots?.some(root => root.status === 'scanning');
    $('sort-all-locations').disabled = readyCount < 2 || scansPending;
  }
}

async function autoSortClearMatches() {
  if (sortBusy) return;
  const root = sortRoot();
  if (!root) { $('sort-feedback').textContent = 'Approve and scan a folder in File Intelligence first.'; return; }
  // Always generate a fresh preview for the selected root before approval.
  await generateSortPlan();
  if (!currentSortPlan || (root.id !== 'all' && currentSortPlan.root_id !== root.id)) return;
  const count = Math.min(currentSortPlan.automatic_count || 0, currentSortPlan.batch_limit || 500);
  if (!count) { $('sort-feedback').textContent = 'No clear matches remain. The files below need your advice or are already organized.'; return; }
  const destinations = sortGroups({...currentSortPlan, suggestions:currentSortPlan.suggestions.filter(s => s.automation_eligible).slice(0,count)}).map(group => `${sortFolderPath({path:group.rootPath}, group.folder)}: ${group.items.length} files`).join('\n');
  const approvalRevision = filesRevision;
  const approvalScope = sortScopeFingerprint();
  setSortScopeControlsDisabled(true);
  if (!await requestSortApproval(`Auto-sort ${count} clear matches?`, `${root.path}\n\n${destinations}\n\nOnly loose files with clear destinations move. Folders are created as needed. Ambiguous files stay untouched; nothing is deleted. Eligible moves can be undone.`)) {
    setSortScopeControlsDisabled(false);
    return;
  }
  if (approvalRevision !== filesRevision || approvalScope !== sortScopeFingerprint()) {
    setSortScopeControlsDisabled(false);
    $('sort-feedback').textContent = 'The scope changed while approval was open. No files were moved; preview again.';
    return;
  }
  sortBusy = true;
  $('sort-auto').disabled = true;
  $('sort-generate').disabled = true;
  try {
    const result = await post('/api/files/sort/auto', {plan_id: currentSortPlan.id, confirm: true});
    currentSortPlan = result.plan;
    renderSortPlan(currentSortPlan);
    $('sort-feedback').textContent = `Sorted ${result.applied.length} files. ${result.failed.length} could not move; ${result.plan.review_count} need your advice. Undo is available.`;
    $('sort-feedback').style.color = result.failed.length ? '#f0aaa0' : '';
  } catch (error) {
    $('sort-feedback').textContent = `Sorting could not finish: ${error.message}`;
  } finally {
    sortBusy = false;
    setSortScopeControlsDisabled(false);
    await refreshFiles();
  }
}

async function applySortPlan(selectedIds = null) {
  if (!currentSortPlan || sortBusy) return;
  const chosen = new Set(selectedIds || currentSortPlan.suggestions.map(suggestion => suggestion.id));
  const chosenSuggestions = currentSortPlan.suggestions.filter(suggestion => chosen.has(suggestion.id));
  if (!chosenSuggestions.length) return;
  const root = {path:chosenSuggestions[0].destination_root_path || currentSortPlan.root_path};
  const destination = chosenSuggestions[0].destination_relative;
  const question = chosenSuggestions.length === 1
    ? `Approve moving ${chosenSuggestions[0].source_relative} to ${root ? sortFolderPath(root, destination.split(/[\\/]/).slice(0, -1).join('/')) : destination}?`
    : `Approve moving ${chosenSuggestions.length} files to their reviewed destinations?`;
  const approvalRevision = filesRevision;
  const approvalScope = sortScopeFingerprint();
  setSortScopeControlsDisabled(true);
  if (!await requestSortApproval('Move reviewed files?', question)) {
    setSortScopeControlsDisabled(false);
    return;
  }
  if (approvalRevision !== filesRevision || approvalScope !== sortScopeFingerprint()) {
    setSortScopeControlsDisabled(false);
    $('sort-feedback').textContent = 'The scope changed while approval was open. No files were moved; preview again.';
    return;
  }
  sortBusy = true;
  filesRevision += 1;
  try {
    const result = await post('/api/files/sort/approve-idea', {
      plan_id: currentSortPlan.id,
      suggestion_ids: chosenSuggestions.map(s => s.id),
      confirm: true,
    });
    currentSortPlan = result.plan;
    renderSortPlan(currentSortPlan);
    $('sort-feedback').textContent = `Sort AI finished: ${result.applied.length} applied, ${result.failed.length} failed. Original files were not deleted.`;
  } catch (error) {
    $('sort-feedback').textContent = error.message;
    $('sort-feedback').style.color = '#f0aaa0';
  } finally {
    sortBusy = false;
    setSortScopeControlsDisabled(false);
    await refreshFiles();
  }
}

async function undoSortPlan() {
  if (!currentSortPlan || sortBusy) return;
  setSortScopeControlsDisabled(true);
  if (!await requestSortApproval('Undo this sorting batch?', 'Restore eligible file moves to their original locations. Paths that changed will not be overwritten.')) {
    setSortScopeControlsDisabled(false);
    return;
  }
  sortBusy = true;
  filesRevision += 1;
  try {
    const result = await post('/api/files/sort/undo', { plan_id: currentSortPlan.id });
    currentSortPlan = result.plan;
    renderSortPlan(currentSortPlan);
    $('sort-feedback').textContent = `Sort AI undo: ${result.rolled_back.length} restored, ${result.failed.length} need review.`;
  } catch (error) {
    $('sort-feedback').textContent = error.message;
    $('sort-feedback').style.color = '#f0aaa0';
  } finally {
    sortBusy = false;
    setSortScopeControlsDisabled(false);
    await refreshFiles();
  }
}

async function keepSortFiles(ids) {
  if (!currentSortPlan || sortBusy) return;
  sortBusy = true;
  filesRevision += 1;
  try {
    currentSortPlan = await post('/api/files/sort/keep', {plan_id:currentSortPlan.id, suggestion_ids:ids, confirm:true});
    renderSortPlan(currentSortPlan);
    $('sort-feedback').textContent = `Keeping ${ids.length} file(s) here. Sort AI will remember this until their indexed size or timestamp changes.`;
  } catch (error) {
    $('sort-feedback').textContent = error.message;
  } finally {
    sortBusy = false;
    await refreshFiles();
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

  const reviews = $('file-insights-reviews');
  reviews.replaceChildren();
  const decisions = (insights.file_decisions || insights.review_candidates || []).slice(0, 20);
  if (!decisions.length) {
    reviews.append(element('p', 'No indexed files are available for an automatic decision yet.', 'empty'));
  } else {
    for (const file of decisions) {
      const card = element('article', null, 'file-review-card');
      const evidence = (file.decision_evidence || []).slice(0, 2).join(' ');
      card.append(
        element('strong', `Automatic decision: ${file.recommended_action_label || 'Review this file'}`),
        element('span', file.name),
        element('code', file.path),
        element('p', file.decision_summary || file.review_reason || 'No extra action is recommended.', file.review_required ? 'sort-warning' : 'sort-content-summary'),
        element('small', file.content_summary, 'quiet'),
        element('small', `Confidence: ${file.decision_confidence || 'medium'} · ${evidence}`, 'sort-content-summary'),
        element('small', file.review_recommendation || file.recommended_action_label || '', 'sort-content-summary'),
        button('Preview & decide', () => openFileReview(file.file_id), 'primary'),
      );
      reviews.append(card);
    }
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

function releaseFilePreview() {
  filePreviewRequest += 1;
  if (filePreviewController) filePreviewController.abort();
  filePreviewController = null;
  const image = $('file-review-image');
  image.onload = null;
  image.onerror = null;
  image.hidden = true;
  image.removeAttribute('src');
  if (filePreviewImageUrl) URL.revokeObjectURL(filePreviewImageUrl);
  filePreviewImageUrl = null;
  if (filePreviewPdfUrl) URL.revokeObjectURL(filePreviewPdfUrl);
  filePreviewPdfUrl = null;
  $('file-review-image-panel').hidden = true;
  $('file-review-image-stage').classList.remove('actual-size');
  $('file-review-image-zoom').textContent = 'View at actual size';
  $('file-review-image-zoom').disabled = true;
  const pdf = $('file-review-pdf');
  pdf.removeAttribute('src');
  $('file-review-pdf-panel').hidden = true;
}

async function loadFileReviewImage(file, requestId, signal) {
  const response = await fetch(file.image_preview_url, {
    cache: 'no-store', headers: { 'X-Kshetrajna-Token': token }, signal,
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || 'Could not load the image');
  }
  const blob = await response.blob();
  if (requestId !== filePreviewRequest || signal.aborted) return;
  const image = $('file-review-image');
  filePreviewImageUrl = URL.createObjectURL(blob);
  image.alt = `Preview of ${file.name}`;
  image.onload = () => {
    if (requestId !== filePreviewRequest) return;
    const format = response.headers.get('X-Image-Format') || file.extension.toUpperCase();
    const dimensions = `${image.naturalWidth} × ${image.naturalHeight} pixels`;
    const description = `${format} image · ${dimensions} · ${response.headers.get('X-Image-Orientation') || ''}`;
    $('file-review-image-status').textContent = 'Image loaded locally. Use actual size to examine small details.';
    $('file-review-summary').textContent = description;
    $('file-review-metadata').textContent = `${bytes(blob.size)} · ${dimensions} · modified ${timeLabel(file.modified_at)}`;
    $('file-review-image-zoom').disabled = false;
    file.insights.content_summary = description;
    file.insights.decision_evidence = [...(file.insights.decision_evidence || []), description];
  };
  image.onerror = () => {
    if (requestId !== filePreviewRequest) return;
    image.hidden = true;
    $('file-review-image-status').textContent = 'The browser could not decode this image; it may be damaged.';
  };
  image.hidden = false;
  image.src = filePreviewImageUrl;
}

async function loadFileReviewPdf(file, requestId, signal) {
  const response = await fetch(file.pdf_preview_url, {
    cache: 'no-store', headers: { 'X-Kshetrajna-Token': token }, signal,
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || 'Could not load the PDF');
  }
  const blob = await response.blob();
  if (requestId !== filePreviewRequest || signal.aborted) return;
  filePreviewPdfUrl = URL.createObjectURL(blob);
  $('file-review-pdf').src = filePreviewPdfUrl;
  const pages = response.headers.get('X-PDF-Pages');
  $('file-review-pdf-status').textContent = pages && pages !== 'unknown'
    ? `PDF loaded locally · ${pages} page${pages === '1' ? '' : 's'}`
    : 'PDF loaded locally';
  $('file-review-summary').textContent = `PDF document · ${pages && pages !== 'unknown' ? `${pages} page${pages === '1' ? '' : 's'}` : 'page count unavailable'}`;
  $('file-review-metadata').textContent = `${bytes(blob.size)} · PDF${pages && pages !== 'unknown' ? ` · ${pages} pages` : ''} · modified ${timeLabel(file.modified_at)}`;
}

function renderTextPreview(text, kind) {
  const preview = $('file-review-excerpt');
  preview.replaceChildren();
  preview.classList.toggle('code-preview', ['source code', 'configuration'].includes(kind));
  if (!text) {
    preview.textContent = 'No readable text preview is available for this file type.';
    return;
  }
  text.split('\n').forEach((line, index) => {
    const row = document.createElement('span');
    row.className = 'file-review-code-line';
    row.dataset.line = String(index + 1);
    row.textContent = line || ' ';
    preview.append(row);
  });
}

async function openFileReview(fileId) {
  releaseFilePreview();
  pendingFileReview = null;
  const requestId = filePreviewRequest;
  filePreviewController = new AbortController();
  const signal = filePreviewController.signal;
  try {
    const detail = await request('/api/files/file/' + encodeURIComponent(fileId) + '/excerpt', { signal });
    if (requestId !== filePreviewRequest) return;
    const file = detail.file;
    pendingFileReview = { file, detail };
    $('file-review-title').textContent = file.name;
    $('file-review-path').textContent = file.path || file.relative_path;
    $('file-review-summary').textContent = file.insights?.content_summary || 'No content summary is available.';
    $('file-review-reason').textContent = file.insights?.review_reason || 'No warning signal was found. Review the preview and decide what you want to do.';
    renderTextPreview(detail.excerpt, file.insights?.content_kind || 'text');
    $('file-review-excerpt').hidden = Boolean(file.image_preview_url || file.pdf_preview_url);
    if (file.image_preview_url) {
      $('file-review-image-panel').hidden = false;
      $('file-review-image-status').textContent = 'Loading image from your approved folder…';
      $('file-review-summary').textContent = 'Opening the actual image inside this preview…';
    }
    if (file.pdf_preview_url) {
      $('file-review-pdf-panel').hidden = false;
      $('file-review-pdf-status').textContent = 'Loading PDF from your approved folder…';
      $('file-review-summary').textContent = 'Opening the PDF inside this preview…';
    }
    $('file-review-metadata').textContent = `${bytes(file.size_bytes)} · ${file.extension ? file.extension.toUpperCase() : 'file'} · modified ${timeLabel(file.modified_at)}`;
    if (!$('file-review-dialog').open) $('file-review-dialog').showModal();
    if (file.image_preview_url) await loadFileReviewImage(file, requestId, signal);
    if (file.pdf_preview_url) await loadFileReviewPdf(file, requestId, signal);
  } catch (error) {
    if (signal.aborted || requestId !== filePreviewRequest) return;
    if (pendingFileReview?.file.image_preview_url) {
      $('file-review-image-status').textContent = error.message;
      $('file-review-summary').textContent = 'Image preview unavailable.';
      return;
    }
    if (pendingFileReview?.file.pdf_preview_url) {
      $('file-review-pdf-status').textContent = error.message;
      $('file-review-summary').textContent = 'PDF preview unavailable.';
      return;
    }
    setFeedback(`Could not preview the file: ${error.message}`, true);
  }
}

function closeFileReviewAndChooseAction() {
  releaseFilePreview();
  const dialog = $('file-review-dialog');
  if (dialog?.open) dialog.close();
  if (!pendingFileReview) return;
  const { file, detail } = pendingFileReview;
  $('file-review-action-copy').textContent = `${file.name}\nAutomatic decision: ${file.insights?.recommended_action_label || 'Review manually'}\n${file.insights?.review_reason || detail.excerpt || 'Review the file manually before deciding.'}\n\nRecommendation: ${file.insights?.review_recommendation || 'Keep it unless you have a separate reason to move or delete it.'}`;
  $('file-review-action-dialog').showModal();
}

function finishFileReviewDecision(decision) {
  const review = pendingFileReview;
  pendingFileReview = null;
  const dialog = $('file-review-action-dialog');
  if (dialog?.open) dialog.close();
  if (!review) return;
  if (decision === 'keep') {
    setFeedback(`Keeping ${review.file.name}; no file change was made.`);
  } else if (decision === 'move') {
    proposeFileAction(review.file, 'MOVE');
  } else if (decision === 'delete') {
    proposeFileAction(review.file, 'DELETE');
  }
}

async function useAutomaticFileDecision() {
  if (!pendingFileReview) return;
  const review = pendingFileReview;
  const action = review.file.insights?.recommended_action;
  if (action === 'keep') {
    finishFileReviewDecision('keep');
    return;
  }
  if (action === 'cleanup') {
    finishFileReviewDecision('delete');
    return;
  }
  if (!['organize', 'archive'].includes(action)) {
    finishFileReviewDecision('keep');
    return;
  }
  const dialog = $('file-review-action-dialog');
  if (dialog?.open) dialog.close();
  try {
    const result = await post('/api/files/organize', { file_id: review.file.id, confirm: true });
    pendingFileReview = null;
    setFeedback(`Automatic decision applied: ${result.applied?.length || 0} file action(s) recorded.`);
    await refreshFiles();
  } catch (error) {
    setFeedback(`Automatic organization failed: ${error.message}`, true);
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
  if (await requestRootApproval(path, label)) {
    await mutateFile('/api/files/roots/add', { path, label, consent: true }, 'Root approved and scan started.');
    $('root-path').value = '';
    $('root-label').value = '';
  }
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
$('sort-confirm-approve').addEventListener('click', () => finishSortApproval(true));
$('sort-confirm-cancel').addEventListener('click', () => finishSortApproval(false));
$('sort-confirm-dialog').addEventListener('cancel', event => {event.preventDefault(); finishSortApproval(false);});
$('sort-auto').addEventListener('click', autoSortClearMatches);
$('sort-root').addEventListener('change', () => {
  filesRevision += 1;
  currentSortPlan = null;
  const allSelected = $('sort-root').value === 'all';
  $('sort-all-locations').checked = allSelected;
  $('sort-all-locations').closest('.sort-all-toggle')?.classList.toggle('selected', allSelected);
  renderSortPlan(null);
  renderDownloadsHint(currentFilesState);
  $('sort-feedback').textContent = '';
});
$('sort-all-locations').addEventListener('change', event => {
  const readyRoots = currentFilesState?.roots?.filter(root => ['ready', 'truncated'].includes(root.status)) || [];
  if (event.target.checked) {
    if (readyRoots.length < 2) {
      event.target.checked = false;
      return;
    }
    $('sort-root').value = 'all';
  } else {
    const first = readyRoots[0];
    $('sort-root').value = first ? String(first.id) : '';
  }
  event.target.closest('.sort-all-toggle')?.classList.toggle('selected', event.target.checked);
  filesRevision += 1;
  currentSortPlan = null;
  renderSortPlan(null);
  renderDownloadsHint(currentFilesState);
  $('sort-feedback').textContent = '';
});
function invalidateSortPreview() {
  if (sortBusy) return;
  filesRevision += 1;
  currentSortPlan = null;
  sortRenderSignature = '';
  renderSortPlan(null);
  renderDownloadsHint(currentFilesState);
  $('sort-feedback').textContent = 'Sorting preferences changed. Preview again to update destinations.';
}
$('prefer-existing').addEventListener('change', invalidateSortPreview);
$('route-downloads-to-desktop').addEventListener('change', invalidateSortPreview);
$('include-nested').addEventListener('change', invalidateSortPreview);
$('sort-history').addEventListener('change', async () => {
  const id = $('sort-history').value;
  if (!id || sortBusy) return;
  filesRevision += 1;
  sortBusy = true;
  try {
    currentSortPlan = await request('/api/files/sort/plan/' + encodeURIComponent(id));
    if ([...$('sort-root').options].some(option => Number(option.value) === currentSortPlan.root_id)) $('sort-root').value = String(currentSortPlan.root_id);
    renderSortPlan(currentSortPlan);
  } catch (error) {
    $('sort-feedback').textContent = error.message;
  } finally {
    sortBusy = false;
  }
});
$('file-permission-confirm').addEventListener('click', () => finishRootApproval(true));
$('file-permission-cancel').addEventListener('click', () => finishRootApproval(false));
$('file-permission-dialog').addEventListener('cancel', event => {
  event.preventDefault();
  finishRootApproval(false);
});
$('file-review-close').addEventListener('click', closeFileReviewAndChooseAction);
$('file-review-image-zoom').addEventListener('click', () => {
  const actualSize = $('file-review-image-stage').classList.toggle('actual-size');
  $('file-review-image-zoom').textContent = actualSize ? 'Fit image to preview' : 'View at actual size';
});
$('file-review-dialog').addEventListener('cancel', event => {
  event.preventDefault();
  closeFileReviewAndChooseAction();
});
$('file-review-keep').addEventListener('click', () => finishFileReviewDecision('keep'));
$('file-review-move').addEventListener('click', () => finishFileReviewDecision('move'));
$('file-review-delete').addEventListener('click', () => finishFileReviewDecision('delete'));
$('file-review-auto').addEventListener('click', useAutomaticFileDecision);

async function proposeFileAction(file, kind) {
  if (kind === 'DELETE' && !window.confirm(`Permanently delete ${file.name}? This cannot be undone by Kshetrajna.`)) return;
  const promptText = kind === 'RENAME'
    ? `New filename for ${file.name} (the extension must stay the same):`
    : `New relative path for ${file.name}, including the filename:`;
  const initial = kind === 'RENAME' ? file.name : file.relative_path;
  const destination = kind === 'DELETE' ? file.relative_path : window.prompt(promptText, initial);
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
    if (kind !== 'DELETE' && !window.confirm(`Review ${kind.toLowerCase()} proposal:\n${description}\n\nApply this file action?`)) {
      await post('/api/files/actions/dismiss', { proposal_id: proposal.proposal_id });
      setFeedback('File action dismissed.');
      await refreshFiles();
      return;
    }
    const result = await post('/api/files/actions/apply', { proposal_id: proposal.proposal_id });
    if (result.status !== (kind === 'DELETE' ? 'deleted' : 'applied')) throw new Error(result.message || 'The file action did not complete.');
    setFeedback(kind === 'DELETE' ? 'File deleted permanently and recorded.' : `${kind === 'RENAME' ? 'Rename' : 'Move'} applied and recorded.`);
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
            const expected = action.kind === 'DELETE' ? 'deleted' : 'applied';
            if (result.status !== expected) throw new Error(result.message || 'The file action did not complete.');
            setFeedback(action.kind === 'DELETE' ? 'File deleted permanently and recorded.' : 'File action applied and recorded.');
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
