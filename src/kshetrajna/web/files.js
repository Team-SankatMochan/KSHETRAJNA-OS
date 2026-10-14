/* File Intelligence Dashboard */

let currentFilesState = null;
let currentSortPlan = null;
let filesRefreshing = false;
let sortBusy = false;
let filesRevision = 0;
let sortRenderSignature = '';
let pendingSortApproval = null;

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
    if (!groups.has(folder)) groups.set(folder, { folder, items: [] });
    groups.get(folder).items.push(suggestion);
  }
  return [...groups.values()];
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
    const summary = currentSortPlan
      ? summaries.find(plan => plan.id === currentSortPlan.id)
      : summaries.find(plan => plan.root_id === Number($('sort-root').value) && ['preview', 'partial', 'applied', 'undo_partial'].includes(plan.status));
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
      
      item.append(actions);
      rootsList.append(item);
    }
  }

  const sortRoot = $('sort-root');
  const previousRoot = sortRoot.value;
  const readyRoots = state.roots.filter(root => ['ready', 'truncated'].includes(root.status));
  const options = readyRoots.length ? readyRoots.map(root => [`${root.label || 'Approved folder'} · ${root.path}`, String(root.id)]) : [['Add and scan a folder first', '']];
  const optionSignature = JSON.stringify(options);
  if (sortRoot.dataset.options !== optionSignature) {
    sortRoot.replaceChildren();
    for (const [label, value] of options) {
      sortRoot.append(new Option(label, value));
    }
    sortRoot.dataset.options = optionSignature;
    sortRoot.value = readyRoots.some(root => String(root.id) === previousRoot) ? previousRoot : readyRoots.length ? String(readyRoots[0].id) : '';
  }
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
}

function renderSortPlan(plan) {
  const signature = JSON.stringify([plan, $('sort-root').value, currentFilesState?.mode]);
  if (signature === sortRenderSignature) return;
  sortRenderSignature = signature;
  const meta = $('sort-plan-meta');
  const list = $('sort-suggestions');
  const actions = $('sort-actions');
  const status = $('sort-status');
  meta.replaceChildren();
  list.replaceChildren();
  actions.replaceChildren();
  if (!plan) {
    status.textContent = currentFilesState?.mode === 'demo' ? 'SIMULATION / REVIEWED IDEAS' : 'REVIEWED IDEAS';
    list.append(element('p', 'Choose a ready approved folder to hear organization ideas.', 'empty'));
    return;
  }
  status.textContent = `${plan.mode === 'demo' ? 'SIMULATION / ' : ''}${plan.status.replaceAll('_', ' ').toUpperCase()}`;
  const automatic = plan.suggestions.filter(s => s.automation_eligible);
  const needsReview = plan.suggestions.filter(s => !s.automation_eligible && ['suggested', 'blocked', 'failed', 'undo_failed'].includes(s.status));
  const completed = plan.suggestions.filter(s => ['applied', 'rolled_back'].includes(s.status));
  const groups = sortGroups({...plan, suggestions: needsReview.slice(0, 100)});
  meta.append(
    element('strong', `${automatic.length} clear matches · ${needsReview.length} need your advice · ${completed.filter(s => s.status === 'applied').length} moved`),
    element('small', `Auto-sort moves up to ${plan.batch_limit || 500} clear matches per batch. Preview expires ${timeLabel(plan.expires_at)}.`, 'quiet'),
  );
  if (plan.scan_partial) meta.append(element('small', 'Partial scan: only the files indexed in the latest pass are considered.', 'sort-warning'));
  if (plan.error) meta.append(element('p', plan.error, 'sort-warning'));
  if (automatic.length) {
    const details = document.createElement('details');
    details.className = 'sort-auto-summary';
    details.append(element('summary', `Clear matches: inspect destinations (${automatic.length})`));
    for (const group of sortGroups({...plan, suggestions: automatic})) {
      details.append(element('p', `${group.folder}: ${group.items.length} files`));
      details.append(element('small', group.items.slice(0, 10).map(s => s.source_relative).join(', ') + (group.items.length > 10 ? '…' : ''), 'quiet'));
    }
    list.append(details);
  }
  if (!needsReview.length && plan.suggestions.length) list.append(element('p', automatic.length ? 'No ambiguous files need your advice. Use Auto-sort clear matches to move this batch.' : 'No pending sorting decisions. Kept files stay where they are; undo is available for eligible moves.', 'empty'));
  if (needsReview.length > 100) list.append(element('p', 'Showing the first 100 review items. Remaining items stay untouched.', 'quiet'));
  if (!plan.suggestions.length) {
    list.append(element('p', 'I did not find a safe organization idea in this indexed folder.', 'empty'));
  }
  const root = {path: plan.root_path, label: plan.root_label};
  for (const group of groups) {
    const movable = group.items.filter(suggestion => suggestion.status === 'suggested');
    const blocked = group.items.filter(suggestion => suggestion.status === 'blocked');
    const card = element('article', null, `sort-conversation${blocked.length && !movable.length ? ' blocked' : ''}`);
    const noun = group.items.length === 1 ? 'this file' : 'these files';
    const location = root ? sortLocation(root) : 'the approved folder';
    const destination = root ? sortFolderPath(root, group.folder) : group.folder;
    const sentence = group.folder ? `Needs your advice: ${noun}. Suggested folder: ${group.folder}.` : `Needs your advice: ${noun}. Keep here or choose a destination in Preview & decide.`;
    card.append(element('p', sentence), element('code', destination));
    const summaries = [...new Set(group.items.map(suggestion => suggestion.content_summary).filter(Boolean))];
    if (summaries.length) {
      card.append(element('small', `What I saw in the approved preview: ${summaries.join(' ')}`, 'sort-content-summary sort-content-summary-banner'));
    }
    const fileList = document.createElement('ul');
    fileList.className = 'sort-conversation-files';
    for (const suggestion of group.items) {
      const item = document.createElement('li');
      const sourcePath = root ? sortFolderPath(root, suggestion.source_relative) : suggestion.source_relative;
      item.append(
        element('span', suggestion.source_relative),
        element('code', sourcePath, 'sort-source-path'),
      );
      if (suggestion.content_summary) {
        item.append(element('small', suggestion.content_summary, 'sort-content-summary'));
      }
      if (suggestion.review_required && suggestion.review_reason) {
        item.append(element('small', suggestion.review_reason, 'sort-review-note'));
      }
      if (suggestion.file_id) {
        item.append(button('Preview & decide', () => openFileReview(suggestion.file_id), 'text-button'));
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
  const rootId = Number($('sort-root').value);
  if (!Number.isInteger(rootId) || rootId <= 0) return;
  sortBusy = true;
  filesRevision += 1;
  currentSortPlan = null;
  $('sort-generate').disabled = true;
  $('sort-auto').disabled = true;
  try {
    currentSortPlan = await post('/api/files/sort/preview', {
      root_id: rootId,
      create_folders: true,
    });
    renderSortPlan(currentSortPlan);
    $('sort-feedback').textContent = `${currentSortPlan.automatic_count} clear matches can be sorted together; ${currentSortPlan.review_count} files need your advice.`;
    $('sort-feedback').style.color = '';
  } catch (error) {
    $('sort-feedback').textContent = error.message;
    $('sort-feedback').style.color = '#f0aaa0';
  } finally {
    sortBusy = false;
    $('sort-generate').disabled = false;
    $('sort-auto').disabled = false;
  }
}

async function autoSortClearMatches() {
  if (sortBusy) return;
  const root = sortRoot();
  if (!root) { $('sort-feedback').textContent = 'Approve and scan a folder in File Intelligence first.'; return; }
  // Always generate a fresh preview for the selected root before approval.
  await generateSortPlan();
  if (!currentSortPlan || currentSortPlan.root_id !== root.id) return;
  const count = Math.min(currentSortPlan.automatic_count || 0, currentSortPlan.batch_limit || 500);
  if (!count) { $('sort-feedback').textContent = 'No clear matches remain. The files below need your advice or are already organized.'; return; }
  const destinations = sortGroups({...currentSortPlan, suggestions:currentSortPlan.suggestions.filter(s => s.automation_eligible).slice(0,count)}).map(group => `${group.folder}: ${group.items.length} files`).join('\n');
  if (!await requestSortApproval(`Auto-sort ${count} clear matches?`, `${root.path}\n\n${destinations}\n\nOnly loose files with clear destinations move. Folders are created as needed. Ambiguous files stay untouched; nothing is deleted. Eligible moves can be undone.`)) return;
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
    await refreshFiles();
  }
}

async function applySortPlan(selectedIds = null) {
  if (!currentSortPlan || sortBusy) return;
  const chosen = new Set(selectedIds || currentSortPlan.suggestions.map(suggestion => suggestion.id));
  const chosenSuggestions = currentSortPlan.suggestions.filter(suggestion => chosen.has(suggestion.id));
  if (!chosenSuggestions.length) return;
  const root = {path:currentSortPlan.root_path};
  const destination = chosenSuggestions[0].destination_relative;
  const question = chosenSuggestions.length === 1
    ? `Approve moving ${chosenSuggestions[0].source_relative} to ${root ? sortFolderPath(root, destination.split(/[\\/]/).slice(0, -1).join('/')) : destination}?`
    : `Approve moving ${chosenSuggestions.length} files to their reviewed destinations?`;
  if (!await requestSortApproval('Move reviewed files?', question)) return;
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
    await refreshFiles();
  }
}

async function undoSortPlan() {
  if (!currentSortPlan || sortBusy) return;
  if (!await requestSortApproval('Undo this sorting batch?', 'Restore eligible file moves to their original locations. Paths that changed will not be overwritten.')) return;
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
  renderSortPlan(null);
  $('sort-feedback').textContent = '';
});
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
