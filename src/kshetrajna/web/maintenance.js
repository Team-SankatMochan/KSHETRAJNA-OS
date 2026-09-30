let maintenancePlan = null;

document.addEventListener('kshetrajna-state', event => {
  const state = event.detail;
  const allowed = state.mode === 'demo' || state.settings.allow_maintenance;
  $('maintenance-status').textContent = state.mode === 'demo' ? 'SIMULATION ONLY' : allowed ? 'REVIEW EACH ACTION' : 'ENABLE IN PRIVACY CONTROLS';
  const list = $('maintenance-actions');
  const signature = JSON.stringify([(state.maintenance || []).map(a => a.id), allowed, state.mode]);
  if (list.dataset.signature === signature) return;
  list.dataset.signature = signature;
  list.replaceChildren();
  for (const action of state.maintenance || []) {
    const card = element('article', null, 'recommendation');
    card.append(element('span', action.action === 'trim_memory' ? 'MEMORY / ONE PROCESS' : 'WINDOWS MAINTENANCE', 'eyebrow'),
      element('h3', action.title), element('p', action.effect), element('small', action.undo));
    const controls = element('div', null, 'control-actions');
    const review = button(state.mode === 'demo' ? 'Review simulation' : 'Review action', async () => {
      try {
        const result = await post('/api/maintenance/preview', {id: action.id});
        maintenancePlan = result.plan;
        $('maintenance-title').textContent = result.plan.title;
        $('maintenance-effect').textContent = result.plan.effect;
        $('maintenance-undo').textContent = result.plan.undo;
        $('maintenance-mode').textContent = state.mode === 'demo' ? 'Simulation: no Windows changes.' : 'Live action on this PC. Approval expires in 60 seconds.';
        $('maintenance-dialog').showModal();
      } catch (error) { setFeedback(error.message, true); }
    }, 'primary');
    review.disabled = !allowed;
    controls.append(review);
    card.append(controls);
    list.append(card);
  }
});

$('maintenance-cancel').addEventListener('click', () => {
  maintenancePlan = null;
  $('maintenance-dialog').close();
});
$('maintenance-confirm').addEventListener('click', async () => {
  if (!maintenancePlan) return;
  $('maintenance-confirm').disabled = true;
  try {
    const result = await post('/api/maintenance/execute', {id: maintenancePlan.id});
    maintenancePlan = null;
    $('maintenance-dialog').close();
    await refresh();
    setFeedback(result.decision.result.message, result.decision.status === 'maintenance_failed');
  } catch (error) { setFeedback(error.message, true); }
  finally { $('maintenance-confirm').disabled = false; }
});
