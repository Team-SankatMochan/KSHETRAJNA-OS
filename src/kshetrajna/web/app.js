const token = document.querySelector('meta[name="csrf-token"]').content;
const $ = (id) => document.getElementById(id);
const fmt = (value, digits = 1) => Number(value).toFixed(digits);
const bytes = (value) => {
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) return 'Unknown size';
  if (size < 1024) return `${Math.round(size)} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  const unit = Math.min(Math.floor(Math.log(size) / Math.log(1024)) - 1, units.length - 1);
  return `${fmt(size / (1024 ** (unit + 1)))} ${units[unit]}`;
};
let currentSettings = null;
let currentMode = 'live';
let refreshing = false;
let toastTimer;
let appStopped = false;

async function request(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
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
    const message = document.createElement('span');
    message.className = 'empty';
    message.textContent = 'Trend appears after two samples.';
    chart.append(message);
    return;
  }
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 600 150');
  svg.setAttribute('preserveAspectRatio', 'none');
  for (const [color, values] of [
    ['#b4edb4', history.map((s) => s.cpu_percent)],
    ['#759bff', history.map((s) => 100 * (1 - s.memory_available_bytes / s.memory_total_bytes))],
  ]) {
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', color);
    line.setAttribute('stroke-width', '2.5');
    line.setAttribute('vector-effect', 'non-scaling-stroke');
    line.setAttribute('points', values.map((v, i) => `${600 * i / (values.length - 1)},${150 - Math.min(100, Math.max(0, v)) * 1.5}`).join(' '));
    svg.append(line);
  }
  chart.append(svg);
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
    for (const value of [process.name, `${fmt(process.cpu_percent)}%`, bytes(process.working_set_bytes)]) {
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
  $('cpu').textContent = latest ? `${fmt(latest.cpu_percent)}%` : '—';
  $('cpu-bar').style.width = latest ? `${latest.cpu_percent}%` : '0%';
  const used = latest ? 100 * (1 - latest.memory_available_bytes / latest.memory_total_bytes) : 0;
  $('memory').textContent = latest ? `${fmt(used)}%` : '—';
  $('memory-bar').style.width = `${used}%`;
  $('memory-detail').textContent = latest ? `${bytes(latest.memory_total_bytes - latest.memory_available_bytes)} of ${bytes(latest.memory_total_bytes)} used` : 'Physical memory';
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
  try { render(await request('/api/state')); }
  catch (error) { setFeedback(`Dashboard update failed: ${error.message}`, true); }
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
      sample_interval_seconds: Number(form.elements.sample_interval_seconds.value),
      retention_days: Number(form.elements.retention_days.value),
    });
    await refresh();
    setFeedback('Preferences saved.');
  } catch (error) { setFeedback(error.message, true); }
});
$('delete-button').addEventListener('click', async () => {
  if (!window.confirm('Restore active trials and delete recorded history, saved workspaces, and learned patterns?')) return;
  try { await post('/api/delete'); await refresh(); setFeedback('Recorded history deleted.'); }
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
