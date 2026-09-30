let latestState = null;
let pendingProposal = null;

function element(tag, text, className = '') {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(text, action, className = 'secondary') {
  const node = element('button', text, className);
  node.type = 'button';
  node.addEventListener('click', action);
  return node;
}

async function mutate(path, payload, message) {
  try {
    const result = await post(path, payload);
    await refresh();
    if (result.decision?.status === 'restore_failed') throw new Error(result.decision.error);
    if (message) setFeedback(message);
    return result;
  } catch (error) { setFeedback(error.message, true); return null; }
}

function renderIntelligence(state) {
  latestState = state;
  const demo = state.mode === 'demo';
  $('mode-label').textContent = demo ? 'SYNTHETIC DEMO / No Windows changes' : 'LIVE / Local Windows observations';
  $('mode-detail').textContent = demo ? 'Seeded app activity shows the entire workflow immediately. All approvals are simulated. Metrics are not benchmarks.' : 'Collection starts with your consent. Live priority trials require an extra opt-in and individual approval.';
  $('scenario-control').hidden = !demo;
  const model = state.intelligence;
  $('context-name').textContent = model.context;
  $('context-confidence').textContent = model.confidence + '% rule vote';
  $('context-explanation').textContent = model.explanation;
  $('active-minutes').textContent = model.active_minutes;
  $('sample-count').textContent = model.sample_count;
  $('switch-count').textContent = model.switches;
  $('pressure').textContent = model.pressure + ' pressure';
  $('pressure').classList.toggle('warning', model.pressure === 'High');
  $('next-app').textContent = model.prediction?.app || 'Gathering observations';
  $('next-evidence').textContent = model.prediction ? model.prediction.confidence + '/100 evidence score · ' + model.prediction.evidence : model.prediction_reason;
  $('next-alternatives').textContent = model.prediction?.alternatives?.length ? 'Other possibilities: ' + model.prediction.alternatives.map(a => a.app + ' (' + a.score + '/100)').join(', ') : '';
  const quality = model.prediction_quality;
  $('prediction-quality').textContent = quality?.predicted
    ? 'History replay: ' + quality.correct + '/' + quality.predicted + ' predictions correct (' + quality.accuracy_percent + '%). Predictions offered for ' + quality.coverage_percent + '% of ' + quality.evaluated + ' switches. Uses only earlier visits; synthetic results are not real-world accuracy.'
    : 'History replay will measure predictions once enough completed visits are available.';
  if (quality?.baseline_predicted) $('prediction-quality').textContent += ' Current-app-only baseline: ' + quality.baseline_correct + '/' + quality.baseline_predicted + ' correct; ' + quality.baseline_predicted + ' predictions offered.';
  const ranks = $('rankings');
  ranks.replaceChildren();
  for (const app of model.rankings.slice(0, 5)) {
    const row = element('div', null, 'rank-row');
    const name = element('div');
    name.append(element('strong', app.app), element('small', app.reason));
    row.append(name, element('span', String(app.importance), 'rank-score'));
    ranks.append(row);
  }
  if (!model.rankings.length) ranks.append(element('p', 'App importance will appear as observations arrive.', 'empty'));
  const recommendations = $('recommendations');
  const signature = JSON.stringify([state.recommendations.map(p => p.id), state.active_actions.length, state.settings.enabled]);
  if (recommendations.dataset.signature !== signature) {
  recommendations.dataset.signature = signature;
  recommendations.replaceChildren();
  for (const proposal of state.recommendations) {
    const card = element('article', null, 'recommendation');
    card.append(element('span', proposal.kind === 'priority' ? 'CPU PRIORITY TRIAL' : proposal.kind.toUpperCase(), 'eyebrow'),
                element('h3', proposal.title), element('p', proposal.reason), element('small', proposal.effect));
    const actions = element('div', null, 'control-actions');
    if (proposal.kind === 'priority') {
      const review = button(demo ? 'Review simulated trial' : 'Review trial', () => openApproval(latestState.recommendations.find(p => p.id === proposal.id) || proposal), 'primary');
      review.disabled = !!state.active_actions.length || !state.settings.enabled;
      actions.append(review);
    } else if (proposal.kind === 'workspace') {
      actions.append(button('Build workspace', () => $('workspace-form').scrollIntoView({ behavior: 'smooth', block: 'center' })));
    }
    actions.append(button('Dismiss', () => mutate('/api/actions/dismiss', { id: proposal.id }, 'Recommendation dismissed.'), 'text-button'));
    card.append(actions);
    recommendations.append(card);
  }
  if (!state.recommendations.length) recommendations.append(element('p', 'No adaptation is recommended right now. Collect more observations to see suggestions.', 'empty'));
  }
  renderWorkspaces(state);
  renderDecisions(state);
  if (state.health.last_error) $('status-text').textContent = 'Collection needs attention';
}

function openApproval(proposal) {
  pendingProposal = proposal;
  $('approval-title').textContent = proposal.title;
  $('approval-reason').textContent = proposal.reason;
  $('approval-effect').textContent = proposal.effect;
  $('approval-mode').textContent = latestState.mode === 'demo'
    ? 'SIMULATION: this records a demo decision. It cannot modify Windows.'
    : 'LIVE ACTION: this changes the selected process CPU priority. It may slow that application. It does not free RAM.';
  $('approval-dialog').showModal();
}

function renderWorkspaces(state) {
  const apps = state.intelligence.rankings.slice(0, 8).map(a => a.app).sort();
  const choices = $('workspace-choices');
  if (choices.dataset.apps !== JSON.stringify(apps)) {
    choices.dataset.apps = JSON.stringify(apps);
    choices.replaceChildren();
    for (const app of apps) {
      const label = element('label');
      const input = document.createElement('input');
      input.type = 'checkbox'; input.value = app; input.checked = true;
      label.append(input, document.createTextNode(app));
      choices.append(label);
    }
  }
  const workflows = $('learned-workflows');
  workflows.replaceChildren();
  for (const group of state.intelligence.workflows) {
    workflows.append(element('p', group.apps.join(' + ') + ' · ' + group.evidence, 'control-note'));
  }
  if (!state.intelligence.workflows.length) workflows.append(element('p', 'Recurring app groups appear after two active five-minute windows. Idle time and brief focus flashes are excluded.', 'empty'));
  const sequences = $('learned-sequences');
  sequences.replaceChildren();
  for (const sequence of state.intelligence.sequences || []) {
    sequences.append(element('p', sequence.apps.join(' → ') + ' · ' + sequence.occurrences + ' repeats across ' + sequence.sessions + ' sessions', 'control-note'));
  }
  if (!state.intelligence.sequences?.length) sequences.append(element('p', 'Ordered routines appear after three repetitions.', 'empty'));
  const saved = $('saved-workspaces');
  const signature = JSON.stringify([state.workspaces, state.active_workspace]);
  if (saved.dataset.signature === signature) return;
  saved.dataset.signature = signature;
  saved.replaceChildren();
  for (const workspace of state.workspaces) {
    const active = workspace.name === state.active_workspace;
    saved.append(button((active ? '● ' : '') + workspace.name, () => mutate('/api/workspaces/activate', { name: workspace.name }, 'Workspace board activated.'), active ? 'primary' : 'secondary'));
  }
  const board = $('workspace-board');
  board.replaceChildren();
  const active = state.workspaces.find(w => w.name === state.active_workspace);
  if (active) {
    board.append(element('p', 'ACTIVE / ' + active.name, 'eyebrow'));
    const tiles = element('div', null, 'board-tiles');
    for (const app of active.apps) {
      const tile = element('div', null, 'app-tile');
      tile.append(element('span', app.charAt(0).toUpperCase(), 'tile-icon'), element('strong', app));
      tiles.append(tile);
    }
    board.append(tiles);
  } else board.append(element('p', 'Activate a saved workspace to arrange your app board.', 'empty'));
}

function renderDecisions(state) {
  const journal = $('decision-journal');
  const signature = JSON.stringify(state.decisions.slice(0, 8));
  if (journal.dataset.signature === signature) {
    for (const node of journal.querySelectorAll('[data-expires]')) {
      node.textContent = 'Restoration due in ' + Math.max(0, Math.ceil(Number(node.dataset.expires) - Date.now() / 1000)) + 's.';
    }
    return;
  }
  journal.dataset.signature = signature;
  journal.replaceChildren();
  for (const decision of state.decisions.slice(0, 8)) {
    const item = element('article', null, 'journal-entry');
    if (decision.kind === 'maintenance') {
      item.append(element('span', (decision.mode === 'demo' ? 'SIMULATED / ' : '') + decision.status.replaceAll('_', ' ').toUpperCase(), 'eyebrow'),
        element('strong', decision.title), element('small', timeLabel(decision.created_at)),
        element('p', decision.result?.message || 'Outcome not recorded.', 'control-note'));
      if (decision.result?.resident_reduction_mb != null) item.append(element('p', 'Immediate resident-memory reduction: ' + decision.result.resident_reduction_mb + ' MiB. This may be temporary.', 'control-note'));
      journal.append(item);
      continue;
    }
    const active = ['active', 'prepared', 'restore_failed'].includes(decision.status);
    item.append(element('span', (decision.mode === 'demo' ? 'SIMULATED · ' : '') + decision.status.replaceAll('_', ' ').toUpperCase(), 'eyebrow'));
    item.append(element('strong', decision.plan ? decision.plan.name + ' · CPU priority' : 'Recommendation dismissed'));
    item.append(element('small', timeLabel(decision.created_at)));
    if (active) {
      const remaining = Math.max(0, Math.ceil(decision.expires_at - Date.now() / 1000));
      const countdown = element('p', decision.error || 'Restoration due in ' + remaining + 's.', 'control-note');
      if (!decision.error) countdown.dataset.expires = decision.expires_at;
      item.append(countdown);
      item.append(button('Undo now', () => mutate('/api/actions/undo', { id: decision.id }, 'Trial restoration completed.'), 'primary'));
    } else if (decision.evaluation) {
      const evaluation = decision.evaluation;
      item.append(element('p', evaluation.conclusion, 'control-note'));
      if (evaluation.cpu_delta_points != null) item.append(element('p', 'CPU change: ' + (evaluation.cpu_delta_points > 0 ? '+' : '') + evaluation.cpu_delta_points + ' points · ' + evaluation.samples_after + ' post-trial samples', 'control-note'));
      const feedback = element('div', null, 'control-actions');
      for (const [value, label] of [['helpful', 'Helpful'], ['not_helpful', 'Not helpful']]) {
        feedback.append(button((decision.feedback === value ? '✓ ' : '') + label, () => mutate('/api/actions/feedback', { id: decision.id, value }, 'Feedback saved locally.'), 'text-button'));
      }
      item.append(feedback);
    }
    journal.append(item);
  }
  if (!state.decisions.length) journal.append(element('p', 'Your approvals, dismissals, outcomes, and undo history will appear here.', 'empty'));
}

document.addEventListener('kshetrajna-state', event => renderIntelligence(event.detail));
$('confirm-trial').addEventListener('click', async () => {
  if (!pendingProposal) return;
  $('confirm-trial').disabled = true;
  const result = await mutate('/api/actions/approve', { id: pendingProposal.id }, 'Trial approved. Undo is available in the journal.');
  $('confirm-trial').disabled = false;
  if (result) $('approval-dialog').close();
});
$('cancel-trial').addEventListener('click', () => $('approval-dialog').close());
$('scenario').addEventListener('change', event => mutate('/api/demo/scenario', { scenario: event.target.value }, 'Synthetic scenario loaded.'));
$('workspace-form').addEventListener('submit', async event => {
  event.preventDefault();
  const apps = [...$('workspace-choices').querySelectorAll('input:checked')].map(input => input.value);
  await mutate('/api/workspaces/save', { name: $('workspace-name').value, apps }, 'Workspace saved locally.');
});
$('workspace-reset').addEventListener('click', () => mutate('/api/workspaces/activate', { name: null }, 'Workspace board reset.'));
$('export-button').addEventListener('click', async () => {
  try {
    const report = await request('/api/report');
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url; link.download = 'kshetrajna-' + report.mode + '-report.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setFeedback('Session report downloaded. It includes locally observed app names.');
  } catch (error) { setFeedback(error.message, true); }
});
