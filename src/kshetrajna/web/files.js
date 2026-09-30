/* File Intelligence Dashboard */

let currentFilesState = null;

async function refreshFiles() {
  try {
    const state = await request('/api/files/state');
    renderFiles(state);
    const insights = await request('/api/files/insights');
    renderInsights(insights);
  } catch (error) {
    console.error("Failed to fetch file state:", error);
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
      item.append(element('span', file.relative_path, 'path'));
      
      if (file.excerpt) {
        item.append(element('div', file.excerpt, 'excerpt'));
      }
      resultsDiv.append(item);
    }
  } catch (err) {
    setFeedback('Search failed: ' + err.message, true);
  }
});

// Initial load
refreshFiles();
setInterval(refreshFiles, 5000);
