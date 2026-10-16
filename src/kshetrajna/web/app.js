const token = document.querySelector('meta[name="csrf-token"]').content;
const $ = (id) => document.getElementById(id);
const fmt = (value, digits = 1) => Number(value).toFixed(digits);
const bytes = (value) => {
  if (value == null) return 'Unavailable';
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) return 'Unknown size';
  if (size < 1024) return `${Math.round(size)} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  const unit = Math.min(Math.floor(Math.log(size) / Math.log(1024)) - 1, units.length - 1);
  return `${fmt(size / (1024 ** (unit + 1)))} ${units[unit]}`;
};
let currentSettings = null;
let currentMode = 'live';
let currentHealthState = null;
let refreshing = false;
let toastTimer;
let appStopped = false;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
let uiMode = 'basic';
try { uiMode = localStorage.getItem('kshetrajna.uiMode') === 'expert' ? 'expert' : 'basic'; } catch { /* Local preference storage is optional. */ }
document.body.dataset.uiMode = uiMode;
$('ui-mode').value = uiMode;

function expertView() { return uiMode === 'expert'; }
function permanentDeleteAvailable() {
  return expertView() && currentSettings?.allow_permanent_delete === true;
}

// Keys describe the same action on the same item, never just its list position.
function focusIdentity(node) {
  const keys = [];
  for (let part = node; part && part !== document.body; part = part.parentElement) {
    if (part.id) keys.push(`id:${part.id}`);
    else if (part.dataset?.focusKey) keys.push(`key:${part.dataset.focusKey}`);
  }
  return JSON.stringify(keys);
}
function captureFocus() {
  const node = document.activeElement;
  if (!node || node === document.body) return null;
  return {node, key: focusIdentity(node), start: node.selectionStart, end: node.selectionEnd};
}
function restoreFocus(saved, fallback = $('view-title')) {
  if (!saved) return;
  const candidates = document.querySelectorAll('button, a[href], input, select, textarea, summary, [tabindex]');
  const target = saved.node.isConnected ? saved.node : [...candidates].find(node => focusIdentity(node) === saved.key);
  if (target && !target.disabled && !target.closest('[hidden]') && target.getClientRects().length) {
    target.focus({preventScroll: true});
    if (saved.start != null && target.setSelectionRange && ['text', 'search', 'textarea'].includes(target.type)) {
      target.setSelectionRange(saved.start, saved.end);
    }
  } else fallback?.focus({preventScroll: true});
}
function preserveFocus(renderContent) {
  const saved = captureFocus();
  const result = renderContent();
  if (saved && (!saved.node.isConnected || document.activeElement === document.body)) restoreFocus(saved);
  return result;
}

const dialogLaunchers = new WeakMap();
function openDialog(dialog) {
  dialogLaunchers.set(dialog, captureFocus());
  for (const error of dialog.querySelectorAll('.dialog-feedback, [role="alert"]')) error.textContent = '';
  dialog.showModal();
  const safe = dialog.querySelector('[data-dialog-cancel]');
  safe?.focus();
}
for (const dialog of document.querySelectorAll('dialog')) {
  const heading = dialog.querySelector('h2');
  if (heading) {
    if (!heading.id) heading.id = `${dialog.id}-title`;
    dialog.setAttribute('aria-labelledby', heading.id);
  }
  const description = dialog.querySelector('h2 + p') || dialog.querySelector('p:not(.eyebrow)');
  if (description) {
    if (!description.id) description.id = `${dialog.id}-description`;
    dialog.setAttribute('aria-describedby', description.id);
  }
  let error = dialog.querySelector('[role="alert"]');
  if (!error) {
    error = document.createElement('p');
    error.className = 'feedback dialog-feedback';
    error.setAttribute('role', 'alert');
    dialog.append(error);
  }
  const cancel = [...dialog.querySelectorAll('button')].find(node => /cancel|keep file|keep in quarantine/i.test(node.textContent));
  if (cancel) cancel.dataset.dialogCancel = '';
  dialog.addEventListener('close', () => {
    if (!document.querySelector('dialog[open]')) restoreFocus(dialogLaunchers.get(dialog));
  });
}
$('ui-mode').addEventListener('change', () => {
  uiMode = $('ui-mode').value === 'expert' ? 'expert' : 'basic';
  document.body.dataset.uiMode = uiMode;
  document.body.dataset.permanentDelete = permanentDeleteAvailable() ? 'allowed' : 'blocked';
  try { localStorage.setItem('kshetrajna.uiMode', uiMode); } catch { /* No capability setting is written. */ }
  // Cancel pending advanced approvals when their controls disappear.
  if (!expertView()) {
    for (const id of ['approval-dialog', 'maintenance-dialog', 'chat-action-dialog', 'file-review-action-dialog']) {
      if ($(id).open) { $(id).dispatchEvent(new Event('cancel', {cancelable: true})); if ($(id).open) $(id).close(); }
    }
  }
  setFeedback(`${expertView() ? 'Expert' : 'Basic'} view selected. Permissions are unchanged.`);
});
// Polling displays are inspectable, not a stream of screen-reader announcements.
for (const id of ['sort-scope-summary', 'cleanup-summary', 'cleanup-scope-status', 'cleanup-scope-note', 'cleanup-empty-status', 'cleanup-dup-status', 'cleanup-recovery-status']) {
  $(id)?.removeAttribute('aria-live');
  $(id)?.removeAttribute('aria-atomic');
  $(id)?.removeAttribute('role');
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (path.startsWith('/api/') && !headers.has('X-Kshetrajna-Token')) {
    headers.set('X-Kshetrajna-Token', token);
  }
  const response = await fetch(path, { cache: 'no-store', ...options, headers });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

async function post(path, body = {}) {
  return request(path, { method: 'POST', headers: {
    'Content-Type': 'application/json', 'X-Kshetrajna-Token': token,
  }, body: JSON.stringify(body) });
}

function setFeedback(message, error = false) {
  const modal = document.querySelector('dialog[open]');
  if (error && modal) {
    const inline = modal.querySelector('.dialog-feedback, [role="alert"]');
    if (inline) { inline.textContent = message; return; }
  }
  $('feedback').textContent = message;
  $('feedback').style.color = error ? '#f0aaa0' : '#addbad';
  $('toast').textContent = message;
  $('toast').classList.toggle('error', error);
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5000);
}

function timeLabel(iso) {
  return new Date(iso).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
}

function drawChart(history) {
  const chart = $('chart');
  chart.replaceChildren();
  if (history.length < 2) {
    $('chart-summary').textContent = 'Trend appears after two samples. Missing observations are not zero.';
    const message = document.createElement('span');
    message.className = 'empty';
    message.textContent = 'Trend appears after two samples.';
    chart.append(message);
    return;
  }
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('viewBox', '0 0 600 150');
  svg.setAttribute('preserveAspectRatio', 'none');
  const series = [
    ['CPU', '#b4edb4', history.map(s => counter(s.cpu_percent))],
    ['Physical memory used', '#759bff', history.map(s => physicalUsage(s))],
  ];
  const summary = [];
  for (const [name, color, values] of series) {
    const known = values.filter(v => v != null);
    summary.push(known.length ? `${name}: ${fmt(Math.min(...known))}–${fmt(Math.max(...known))}%; latest available ${fmt(known.at(-1))}%. ${values.length - known.length} unavailable.` : `${name}: unavailable.`);
    let points = [];
    const appendSegment = () => {
      if (!points.length) return;
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
      line.setAttribute('fill', 'none');
      line.setAttribute('stroke', color);
      line.setAttribute('stroke-width', '2.5');
      line.setAttribute('vector-effect', 'non-scaling-stroke');
      line.setAttribute('points', points.join(' '));
      svg.append(line);
      points = [];
    };
    values.forEach((v, i) => {
      if (v == null) appendSegment();
      else points.push(`${600 * i / (values.length - 1)},${150 - Math.min(100, Math.max(0, v)) * 1.5}`);
    });
    appendSegment();
  }
  $('chart-summary').textContent = `${history.length} recent observations. ${summary.join(' ')} Gaps are not interpolated. Before/after changes do not establish cause.`;
  chart.append(svg);
}

function counter(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}
function physicalUsage(sample) {
  const total = counter(sample?.memory_total_bytes);
  const available = counter(sample?.memory_available_bytes);
  return total > 0 && available != null && available <= total ? 100 * (1 - available / total) : null;
}
function renderMemory(latest) {
  const total = counter(latest?.memory_total_bytes);
  const available = counter(latest?.memory_available_bytes);
  const used = physicalUsage(latest);
  $('memory').textContent = used == null ? 'Unavailable' : `${fmt(used)}%`;
  $('memory-bar').style.width = `${used ?? 0}%`;
  $('memory-detail').textContent = used == null ? 'Physical memory unavailable' : `${bytes(total - available)} of ${bytes(total)} used`;
  $('memory-physical').textContent = used == null ? 'Unavailable' : `${bytes(total - available)} of ${bytes(total)} (${fmt(used)}%)`;
  $('memory-available').textContent = used == null ? 'Unavailable' : bytes(available);
  const committed = counter(latest?.memory_commit_total_bytes);
  const limit = counter(latest?.memory_commit_limit_bytes);
  $('memory-commit').textContent = `${committed == null ? 'Unavailable' : bytes(committed)} / ${limit == null ? 'Unavailable' : bytes(limit)}`;
  const cache = counter(latest?.memory_system_cache_bytes);
  $('memory-cache').textContent = cache == null ? 'Unavailable' : `${bytes(cache)} · not additional free RAM`;
  const low = latest?.memory_low_signal;
  $('memory-low-signal').textContent = low === true ? 'Low-memory signal reported' : low === false ? 'No low-memory signal reported' : 'Unavailable';
}

function resourceStatus(health, resource) {
  if (health?.freshness !== 'fresh') return 'unknown';
  const status = resource === 'memory_pressure' ? health.memory_pressure : health[resource]?.status;
  return ['normal', 'elevated', 'high'].includes(status) ? status : 'unknown';
}

function healthFreshnessText(health, hasSample, updatesAvailable = true) {
  if (!updatesAvailable) return 'Updates unavailable. Current resource diagnoses are unknown; displayed values are last received observations.';
  if (!hasSample) return 'No samples available. Enable collection in System Health when you are ready. Current resource diagnoses are unknown.';
  const age = counter(health?.age_seconds);
  const ageText = age == null ? 'age unavailable' : `${fmt(age)} seconds old`;
  if (health?.freshness === 'fresh') return `Fresh sample · ${ageText}. Single sampled observations are review cues, not proof of a cause or a performance improvement.`;
  if (health?.freshness === 'stale') return `Stale sample · ${ageText}. Current resource diagnoses are unknown; displayed values are historical.`;
  return 'Sample freshness or resource diagnoses unavailable. Current resource statuses are unknown; a stored number is not a live diagnosis.';
}

function renderResourceHealth(state, updatesAvailable = true) {
  // state.health is collector status; only intelligence.health contains diagnoses.
  const source = state.intelligence?.health;
  const health = updatesAvailable ? source : source ? {...source, freshness: 'unknown'} : null;
  const labels = {};
  for (const [resource, id] of [
    ['cpu', 'health-cpu'], ['physical', 'health-physical'], ['commit', 'health-commit'],
    ['os_low_memory', 'health-os-low-memory'], ['memory_pressure', 'health-memory-pressure'], ['disk', 'health-disk'],
  ]) {
    const status = resourceStatus(health, resource);
    labels[resource] = status[0].toUpperCase() + status.slice(1);
    $(id).textContent = labels[resource];
    $(id).dataset.status = status;
  }
  const cpuValue = counter(source?.cpu ? source.cpu.percent : state.latest?.cpu_percent);
  const cpu = cpuValue != null && cpuValue <= 100 ? cpuValue : null;
  $('cpu').textContent = cpu == null ? 'Unavailable' : `${fmt(cpu)}%`;
  $('cpu-bar').style.width = `${cpu ?? 0}%`;

  const observed = source?.observed_at || state.latest?.observed_at;
  const observedText = observed && Number.isFinite(Date.parse(observed)) ? `Observed ${timeLabel(observed)}` : 'No sample timestamp available';
  const evaluated = source?.evaluated_at;
  const evaluatedText = evaluated && Number.isFinite(Date.parse(evaluated)) ? `Evaluated ${timeLabel(evaluated)}` : 'Diagnosis evaluation timestamp unavailable';
  const freshness = `${state.mode === 'demo' ? 'Synthetic demo. ' : ''}${healthFreshnessText(health, Boolean(state.latest), updatesAvailable)}`;
  $('health-observed-at').textContent = observedText;
  $('health-evaluated-at').textContent = evaluatedText;
  $('health-freshness').textContent = freshness;
  $('memory-observed-at').textContent = observedText;
  $('memory-evaluated-at').textContent = evaluatedText;
  $('memory-freshness').textContent = `${freshness} ${state.settings?.enabled ? 'Collection active; readings may lag.' : 'Collection paused; these are last stored readings.'} Concurrent work can change values.`;
  $('memory-physical-status').textContent = `Current diagnosis: ${labels.physical}`;
  $('memory-commit-status').textContent = `Current diagnosis: ${labels.commit}`;
  $('memory-low-signal-status').textContent = `Current diagnosis: ${labels.os_low_memory}`;

  const pressure = resourceStatus(health, 'memory_pressure');
  const commitPercent = counter(source?.commit?.used_percent);
  const details = `Physical use: ${labels.physical}; commit use: ${labels.commit}${commitPercent == null ? '' : ` (${fmt(commitPercent)}% of limit)`}; OS low-memory signal: ${labels.os_low_memory}.`;
  const memoryNote = resourceStatus(health, 'os_low_memory') === 'high'
    ? 'The OS reports a low-memory signal. Save work and review apps you no longer need.'
    : ['high', 'elevated'].includes(resourceStatus(health, 'commit'))
      ? 'Commit and physical memory are different constraints; low physical use does not cancel commit pressure.'
      : 'Review repeated readings and workload symptoms before changing apps.';
  $('memory-pressure-explanation').textContent = pressure === 'unknown'
    ? `Current memory pressure is unknown: ${health?.freshness === 'fresh' ? 'one or more required counters are unavailable.' : 'a fresh resource diagnosis is unavailable.'} ${details} Stored counters are not a live diagnosis.`
    : `Memory pressure: ${labels.memory_pressure}. ${details} ${memoryNote} These are review cues, not proof that an action will improve performance.`;
  const disk = source?.disk;
  const freePercent = counter(disk?.free_percent);
  const diskFree = counter(disk?.free_bytes), diskTotal = counter(disk?.total_bytes);
  const diskDetail = diskFree == null && diskTotal == null ? 'Capacity unavailable'
    : `${bytes(diskFree)} free of ${bytes(diskTotal)}${freePercent == null ? '' : ` (${fmt(freePercent)}% free)`}`;
  $('health-disk-detail').textContent = diskDetail;
  $('storage-capacity').textContent = diskDetail;
  $('storage-capacity-status').textContent = `Current volume-use diagnosis: ${labels.disk}`;
  $('storage-observed-at').textContent = observedText;
  $('storage-capacity-freshness').textContent = freshness;
}

function renderProcesses(processes) {
  const body = $('processes');
  body.replaceChildren();
  if (!processes.length) {
    const row = body.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 3;
    cell.className = 'empty';
    cell.textContent = 'No process samples yet.';
    return;
  }
  for (const process of processes) {
    const row = body.insertRow();
    for (const value of [process.name, counter(process.cpu_percent) == null ? 'Unavailable' : `${fmt(process.cpu_percent)}%`, bytes(process.working_set_bytes)]) {
      row.insertCell().textContent = value;
    }
  }
}

function renderActivity(events) {
  const list = $('activity');
  list.replaceChildren();
  if (!events.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'No activity events yet.';
    list.append(empty);
    return;
  }
  for (const event of events) {
    const row = document.createElement('div');
    row.className = 'activity-item';
    const title = document.createElement('strong');
    title.textContent = event.kind === 'foreground_changed' ? `Switched to ${event.app_name || 'unknown app'}` : event.kind === 'became_idle' ? 'Became idle' : 'Became active';
    const date = document.createElement('small');
    date.textContent = timeLabel(event.observed_at);
    row.append(title, date);
    list.append(row);
  }
}

function render(state) {
  const settings = state.settings;
  currentMode = state.mode;
  currentHealthState = state;
  const changed = !currentSettings || JSON.stringify(settings) !== JSON.stringify(currentSettings);
  currentSettings = settings;
  $('status').classList.toggle('active', settings.enabled);
  $('status-text').textContent = settings.enabled ? 'Collecting locally' : 'Collection paused';
  $('capture-button').disabled = false;
  $('capture-button').textContent = settings.enabled ? 'Pause collection' : 'Enable collection';
  if (changed) {
    for (const key of ['track_processes', 'track_foreground']) document.querySelector(`[name="${key}"]`).checked = settings[key];
    for (const key of ['sample_interval_seconds', 'retention_days']) document.querySelector(`[name="${key}"]`).value = settings[key];
  }
  const latest = state.latest;
  if (changed) document.querySelector('[name="allow_priority_changes"]').checked = settings.allow_priority_changes;
  if (changed) document.querySelector('[name="allow_maintenance"]').checked = settings.allow_maintenance;
  if (changed) document.querySelector('[name="allow_process_control"]').checked = settings.allow_process_control;
  const deleteSetting = document.querySelector('[name="allow_permanent_delete"]');
  const supportsDelete = typeof settings.allow_permanent_delete === 'boolean';
  deleteSetting.disabled = !supportsDelete;
  if (changed) deleteSetting.checked = settings.allow_permanent_delete === true;
  $('permanent-delete-support').textContent = supportsDelete ? 'Supported. Save an explicit opt-in here before reviewing any permanent deletion. Expert view alone never enables it.' : 'This backend does not expose this capability. Permanent deletion is unavailable.';
  document.body.dataset.permanentDelete = permanentDeleteAvailable() ? 'allowed' : 'blocked';
  renderMemory(latest);
  renderResourceHealth(state);
  $('foreground').textContent = latest?.foreground_app || '—';
  $('idle').textContent = latest?.idle_seconds == null ? 'Foreground tracking unavailable or disabled' : `Last input ${Math.round(latest.idle_seconds)} seconds ago`;
  $('last-seen').textContent = latest ? `Sampled ${timeLabel(latest.observed_at)}` : 'No samples';
  drawChart(state.history);
  renderProcesses(latest?.processes || []);
  renderActivity(state.activity);
  document.dispatchEvent(new CustomEvent('kshetrajna-state', { detail: state }));
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try { const state = await request('/api/state'); preserveFocus(() => render(state)); }
  catch {
    $('status-text').textContent = 'Update unavailable · readings may be stale';
    renderResourceHealth(currentHealthState || {latest: null, settings: {}}, false);
  }
  finally { refreshing = false; }
}

$('capture-button').addEventListener('click', async () => {
  try {
    const enabling = !currentSettings.enabled;
    const payload = { enabled: enabling };
    if (enabling && currentMode !== 'demo' && !currentSettings.device_consent) {
      if (!window.confirm('Allow Kshetrajna to collect local CPU, memory, process-basename, foreground, and idle telemetry? This consent is saved once in your local settings. No keystrokes, clipboard, URLs, titles, screenshots, or document content are collected.')) return;
      payload.device_consent = true;
    }
    await post('/api/settings', payload); await refresh(); setFeedback(currentSettings.enabled ? 'Collection enabled.' : 'Collection paused.');
  }
  catch (error) { setFeedback(error.message, true); }
});
$('settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await post('/api/settings', {
      track_processes: form.elements.track_processes.checked,
      track_foreground: form.elements.track_foreground.checked,
      allow_priority_changes: form.elements.allow_priority_changes.checked,
      allow_maintenance: form.elements.allow_maintenance.checked,
      allow_process_control: form.elements.allow_process_control.checked,
      ...(typeof currentSettings?.allow_permanent_delete === 'boolean' ? {allow_permanent_delete: form.elements.allow_permanent_delete.checked} : {}),
      sample_interval_seconds: Number(form.elements.sample_interval_seconds.value),
      retention_days: Number(form.elements.retention_days.value),
    });
    await refresh();
    setFeedback('Preferences saved.');
  } catch (error) { setFeedback(error.message, true); }
});
$('delete-button').addEventListener('click', async () => {
  if (!window.confirm('Restore active trials and delete recorded history, saved workspaces, and learned patterns?')) return;
  try { await post('/api/delete', {confirm: true}); await refresh(); setFeedback('Recorded history deleted.'); }
  catch (error) { setFeedback(error.message, true); }
});
$('quit-button').addEventListener('click', async () => {
  if (!window.confirm('Quit Kshetrajna? Collection will stop and active priority trials will attempt restoration. Your stored history and files remain.')) return;
  try {
    await post('/api/shutdown', { confirm: true });
    appStopped = true;
    $('quit-button').disabled = true;
    $('capture-button').disabled = true;
    $('status-text').textContent = 'Stopping local app';
    setFeedback('Shutdown requested. The local app is stopping and attempting to restore active trials. Restart it to reconnect.');
  } catch (error) { setFeedback(error.message, true); }
});
refresh();
setInterval(() => { if (!appStopped) refresh(); }, 2000);
