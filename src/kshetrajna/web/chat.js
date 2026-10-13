/* Local chat, plain-text model output, and independently approved tool actions. */
let chatSession = null;
let chatJob = null;
let chatEpoch = 0;
let chatSetup = null;
let chatPlan = null;
let chatLastSettings = '';
let chatCancelRequested = false;

function chatFeedback(message, error = false) {
  $('chat-feedback').textContent = message;
  $('chat-feedback').classList.toggle('chat-error', error);
}

async function refreshChatModels(force = false) {
  try {
    chatSetup = await post('/api/chat/status', {refresh: force});
    const select = $('chat-model');
    select.replaceChildren();
    for (const model of chatSetup.models) {
      const option = document.createElement('option');
      option.value = model.name;
      option.textContent = `${model.name} (${bytes(model.size_bytes)} on disk)`;
      select.append(option);
    }
    if (!chatSetup.models.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = 'No installed local models';
      select.append(option);
    }
    select.value = chatSetup.selected_model;
    $('chat-ai-toggle').textContent = chatSetup.enabled ? 'Pause local AI' : 'Enable local AI';
    $('chat-unload').disabled = !chatSetup.enabled || !chatSetup.models.length;
    $('chat-model-status').textContent = chatSetup.error || (chatSetup.enabled
      ? `Local AI enabled: ${chatSetup.selected_model || 'choose an installed model'}. No cloud calls or API key. ${chatSetup.mode === 'demo' ? 'PC readings and actions are synthetic.' : 'Only collected PC readings can enter this conversation.'}`
      : 'Local tools are ready. Enable local AI for general conversation with an installed model.');
  } catch (error) { chatFeedback(error.message, true); }
}

function chatMessage(role, content = '') {
  $('chat-messages').querySelector('.chat-welcome')?.remove();
  const container = document.createElement('article');
  container.className = `chat-message ${role}`;
  const label = document.createElement('div');
  label.className = 'chat-message-label';
  label.textContent = role === 'user' ? 'You' : 'Kshetrajna';
  const text = document.createElement('div');
  text.className = 'chat-message-text';
  text.textContent = content;
  container.append(label, text);
  $('chat-messages').append(container);
  while ($('chat-messages').children.length > 40) $('chat-messages').firstElementChild.remove();
  return {container, text};
}

function renderChatText(node, content) {
  // A deliberately small Markdown subset, built with text nodes only.
  // Raw HTML, model links, scripts and event attributes are never interpreted.
  node.replaceChildren();
  const inline = value => {
    const pattern = /\*\*([^*\n]+)\*\*|`([^`\n]+)`/g;
    let from = 0;
    for (const match of value.matchAll(pattern)) {
      node.append(document.createTextNode(value.slice(from, match.index)));
      const part = document.createElement(match[1] ? 'strong' : 'code');
      part.textContent = match[1] || match[2];
      node.append(part);
      from = match.index + match[0].length;
    }
    node.append(document.createTextNode(value.slice(from)));
  };
  const blocks = String(content).split(/(```[^\n]*\n[\s\S]*?```)/g);
  for (const block of blocks) {
    if (block.startsWith('```') && block.endsWith('```')) {
      const pre = document.createElement('pre');
      const code = document.createElement('code');
      code.textContent = block.slice(block.indexOf('\n') + 1, -3);
      pre.append(code); node.append(pre);
    } else inline(block);
  }
}

function showChatActions(container, actions) {
  let list = container.querySelector('.chat-action-list');
  if (!list) { list = document.createElement('div'); list.className = 'chat-action-list'; container.append(list); }
  list.replaceChildren();
  for (const action of actions || []) {
    const card = document.createElement('article');
    card.className = 'chat-action-card';
    card.dataset.chatActionKind = action.type;
    card.append(element('strong', action.title));
    if (action.description) card.append(element('p', action.description));
    if (action.type === 'navigate') {
      const link = element('a', 'Open panel'); link.href = '#files'; card.append(link);
    } else if (action.type === 'unload') {
      card.append(button('Review unload', unloadChatModel, 'secondary'));
    } else {
      const review = button(action.type === 'process' ? 'Request close' : 'Review action', () => reviewChatAction(action, 'close'), 'secondary');
      review.disabled = !action.enabled;
      card.append(review);
      if (action.type === 'process') {
        const force = button('Force terminate', () => reviewChatAction(action, 'terminate'), 'danger text-button');
        force.disabled = !action.enabled;
        card.append(force);
      }
      if (!action.enabled) {
        const link = element('a', action.type === 'process' ? 'Enable app controls in Privacy' : 'Enable maintenance in Privacy');
        link.href = '#privacy'; card.append(link);
      }
    }
    list.append(card);
  }
}

async function sendChatMessage(message) {
  if (chatJob) return;
  const epoch = chatEpoch;
  chatJob = 'starting';
  chatCancelRequested = false;
  chatMessage('user', message);
  const reply = chatMessage('assistant', 'Preparing your reply...');
  $('chat-send').disabled = true;
  $('chat-stop').hidden = false;
  $('chat-input').value = '';
  chatFeedback('');
  try {
    let job = await post('/api/chat/message', {message, session_id: chatSession});
    if (epoch !== chatEpoch) { await post('/api/chat/clear', {session_id: job.session_id}); return; }
    chatSession = job.session_id;
    chatJob = job.id;
    if (chatCancelRequested) await post('/api/chat/cancel', {id: chatJob, session_id: chatSession});
    showChatActions(reply.container, job.actions);
    let actionSignature = JSON.stringify(job.actions);
    const provider = element('small', '', 'chat-provider'); reply.container.append(provider);
    const verified = element('p', '', 'chat-verified'); reply.container.append(verified);
    for (let attempt = 0; attempt < 600 && epoch === chatEpoch; attempt++) {
      renderChatText(reply.text, job.reply || 'Local model is thinking...');
      verified.textContent = job.verified_summary ? 'PC review: ' + job.verified_summary : '';
      if (JSON.stringify(job.actions) !== actionSignature) { showChatActions(reply.container, job.actions); actionSignature = JSON.stringify(job.actions); }
      provider.textContent = job.provider + (job.status === 'generating' ? ' · Generating locally' : '');
      if (job.status !== 'generating') {
        if (job.error) reply.container.append(element('p', job.error, 'chat-error'));
        if (job.status === 'cancelled') provider.textContent += ' · Stopped';
        break;
      }
      $('chat-messages').scrollTop = $('chat-messages').scrollHeight;
      await new Promise(resolve => setTimeout(resolve, 600));
      if (epoch !== chatEpoch) break;
      job = await post('/api/chat/job', {id: chatJob, session_id: chatSession});
    }
  } catch (error) {
    if (epoch === chatEpoch) { reply.container.append(element('p', error.message, 'chat-error')); chatFeedback(error.message, true); }
  } finally {
    if (epoch === chatEpoch) { chatJob = null; $('chat-send').disabled = false; $('chat-stop').hidden = true; $('chat-input').focus(); }
  }
}

async function reviewChatAction(action, kind) {
  try {
    const result = action.type === 'process'
      ? await post('/api/processes/preview', {pid: action.pid, action: kind})
      : await post('/api/maintenance/preview', {id: action.offer_id});
    chatPlan = {plan: result.plan, type: action.type};
    const plan = result.plan;
    $('chat-action-title').textContent = plan.title;
    $('chat-action-effect').textContent = plan.effect;
    $('chat-action-undo').textContent = plan.undo;
    $('chat-action-mode').textContent = (plan.mode === 'demo' || currentMode === 'demo') ? 'Simulation only. No real processes or files change.' : 'Live action on this PC. Approval expires after 60 seconds.';
    $('chat-action-name-row').hidden = plan.action !== 'terminate';
    $('chat-action-name').value = '';
    $('chat-action-error').textContent = '';
    const candidates = $('chat-action-candidates'); candidates.replaceChildren();
    candidates.hidden = !plan.candidates?.length;
    for (const candidate of plan.candidates || []) candidates.append(element('small', `${candidate.relative_path} · ${bytes(candidate.size_bytes)}`));
    $('chat-action-dialog').showModal();
  } catch (error) { chatFeedback(error.message, true); }
}

async function unloadChatModel() {
  if (!chatSetup?.selected_model || !window.confirm(`Unload ${chatSetup.selected_model}? The next reply reloads it. This does not close other apps.`)) return;
  try { const result = await post('/api/chat/unload', {model: chatSetup.selected_model, confirm: true}); chatMessage('assistant', result.message); }
  catch (error) { chatFeedback(error.message, true); }
}

$('chat-form').addEventListener('submit', event => { event.preventDefault(); const message = $('chat-input').value.trim(); if (message) sendChatMessage(message); });
$('chat-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('chat-form').requestSubmit(); } });
document.querySelectorAll('[data-chat-prompt]').forEach(node => node.addEventListener('click', () => sendChatMessage(node.dataset.chatPrompt)));
$('chat-model-refresh').addEventListener('click', () => refreshChatModels(true));
$('chat-unload').addEventListener('click', unloadChatModel);
$('chat-model').addEventListener('change', async () => { try { await post('/api/settings', {local_ai_model: $('chat-model').value}); await refreshChatModels(); } catch (error) { chatFeedback(error.message, true); } });
$('chat-ai-toggle').addEventListener('click', async () => {
  if (!chatSetup) return;
  const enable = !chatSetup.enabled;
  if (enable && !window.confirm('Allow local AI conversation? Messages and currently collected PC readings go only to your installed Ollama server on this device. The model uses CPU/RAM. No file excerpts, cloud calls, API key or download are included.')) return;
  try { await post('/api/settings', {allow_local_ai: enable, local_ai_model: $('chat-model').value}); await refreshChatModels(); await refresh(); }
  catch (error) { chatFeedback(error.message, true); }
});
$('chat-stop').addEventListener('click', async () => { chatCancelRequested = true; if (chatJob && chatJob !== 'starting') { try { await post('/api/chat/cancel', {id: chatJob, session_id: chatSession}); } catch (error) { chatFeedback(error.message, true); } } });
$('chat-clear').addEventListener('click', async () => {
  try {
    if (chatSession) await post('/api/chat/clear', {session_id: chatSession});
    chatEpoch++; chatSession = null; chatJob = null;
    $('chat-messages').replaceChildren(); $('chat-send').disabled = false; $('chat-stop').hidden = true;
    chatFeedback('Conversation cleared from this app. The local runtime may keep the model loaded until its idle timeout.');
  } catch (error) { chatFeedback(error.message, true); }
});
$('chat-action-cancel').addEventListener('click', () => { chatPlan = null; $('chat-action-dialog').close(); });
$('chat-action-dialog').addEventListener('cancel', () => { chatPlan = null; });
$('chat-action-confirm').addEventListener('click', async () => {
  if (!chatPlan) return;
  $('chat-action-confirm').disabled = true;
  try {
    const result = chatPlan.type === 'process'
      ? await post('/api/processes/execute', {id: chatPlan.plan.id, confirm: true, typed_name: $('chat-action-name').value, session_id: chatSession})
      : await post('/api/maintenance/execute', {id: chatPlan.plan.id, session_id: chatSession});
    chatMessage('assistant', `${result.decision.title}\n${result.decision.result.message}\n${result.decision.undo || ''}`);
    chatPlan = null; $('chat-action-dialog').close(); await refresh();
  } catch (error) { $('chat-action-error').textContent = error.message; }
  finally { $('chat-action-confirm').disabled = false; }
});
document.addEventListener('kshetrajna-state', event => {
  for (const card of document.querySelectorAll('.chat-action-card')) {
    const type = card.dataset.chatActionKind;
    if (type !== 'process' && type !== 'maintenance') continue;
    const allowed = event.detail.mode === 'demo' || (type === 'process' ? event.detail.settings.allow_process_control : event.detail.settings.allow_maintenance);
    card.querySelectorAll('button').forEach(node => { node.disabled = !allowed; });
    card.querySelectorAll('a[href="#privacy"]').forEach(node => { node.hidden = allowed; });
  }
  const signature = JSON.stringify([event.detail.settings.allow_local_ai, event.detail.settings.local_ai_model, event.detail.settings.allow_process_control]);
  if (chatLastSettings && signature !== chatLastSettings) refreshChatModels();
  chatLastSettings = signature;
});
refreshChatModels();
