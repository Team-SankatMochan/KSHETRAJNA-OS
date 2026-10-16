"""Offline presentation contracts; Node checks use DOM doubles, not a browser.

Run with unittest discovery. Python-only checks always run; behavioral JavaScript
checks run when Node is available. These do not certify browser/a11y conformance.
"""

from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import unittest


WEB = Path(__file__).resolve().parents[1] / "src" / "kshetrajna" / "web"
SOURCES = {path.name: path.read_text(encoding="utf-8") for path in WEB.iterdir()
           if path.suffix in {".html", ".js", ".css"}}


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.feed(SOURCES["index.html"])

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def js_function(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\([^\n]*\) \{{\n.*?^\}}",
                      SOURCES[source], re.M | re.S)
    if not match:
        raise AssertionError(f"Missing named function {source}:{name}")
    return match.group()


def js_between(source, start, end):
    return SOURCES[source].split(start, 1)[1].split(end, 1)[0]


class UIStaticContractTests(unittest.TestCase):
    def test_ids_are_unique_and_existing_controls_resolve(self):
        markup = Markup()
        ids = [attrs["id"] for _, attrs in markup.tags if "id" in attrs]
        self.assertEqual(len(ids), len(set(ids)))
        for filename, source in SOURCES.items():
            if filename.endswith(".js"):
                references = set(re.findall(r"\$\('([^']+)'\)", source))
                self.assertFalse(references - set(ids), filename)

    def test_seven_task_destinations_and_skip_link(self):
        markup = Markup()
        nav = [attrs["data-view"] for _, attrs in markup.tags if "data-view" in attrs]
        self.assertEqual(nav, ["overview", "cleanup", "memory", "sort-ai", "history", "privacy", "chat"])
        self.assertTrue(any(tag == "a" and attrs.get("href") == "#main-content"
                            for tag, attrs in markup.tags))
        self.assertIn("window.addEventListener('hashchange', switchWorkspace)", SOURCES["workspace.js"])
        self.assertIn("aria-current", SOURCES["workspace.js"])
        self.assertIn("pageSurfaces.history.prepend(document.querySelector('.cleanup-recovery'))", SOURCES["workspace.js"])

    def test_expert_is_local_disclosure_not_a_permission(self):
        handler = js_between("app.js", "$('ui-mode').addEventListener('change', () => {", "\n});")
        self.assertNotIn("post(", handler)
        self.assertIn("localStorage.setItem('kshetrajna.uiMode', uiMode)", handler)
        self.assertIn('data-ui-mode="basic"', SOURCES["index.html"])
        self.assertIn('body[data-ui-mode="basic"] [data-expert]', SOURCES["workspace.css"])
        self.assertIn("typeof settings.allow_permanent_delete === 'boolean'", SOURCES["app.js"])
        self.assertIn("currentSettings?.allow_permanent_delete === true", SOURCES["app.js"])

    def test_memory_page_is_read_only_and_truthful(self):
        markup = SOURCES["index.html"].split('<section id="memory-investigation"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn("<button", markup)
        self.assertIn("already reusable", markup)
        self.assertIn("overlaps", markup)
        self.assertIn("does not release committed allocations", markup)
        self.assertIn("does <strong>not free disk capacity</strong>", SOURCES["index.html"])
        self.assertNotIn(">Free up RAM<", SOURCES["index.html"])
        self.assertNotIn("duplicate bytes wasted", SOURCES["files.js"])

    def test_dialogs_use_shared_accessibility_and_focus_setup(self):
        source = SOURCES["app.js"]
        self.assertIn("dialog.setAttribute('aria-labelledby', heading.id)", source)
        self.assertIn("dialog.setAttribute('aria-describedby', description.id)", source)
        self.assertIn("error.setAttribute('role', 'alert')", source)
        self.assertIn("restoreFocus(dialogLaunchers.get(dialog))", source)
        for filename, text in SOURCES.items():
            if filename.endswith(".js") and filename != "app.js":
                self.assertNotIn(".showModal();", text, filename)

    def test_motion_and_polling_are_not_live_announcement_streams(self):
        self.assertIn("prefers-reduced-motion: reduce", SOURCES["app.js"])
        self.assertNotIn("behavior: 'smooth'", SOURCES["intelligence.js"])
        self.assertIn("$('chart-summary').textContent", SOURCES["app.js"])
        self.assertIn("removeAttribute('aria-live')", SOURCES["app.js"])
        mobile = SOURCES["workspace.css"].rsplit("@media (max-width: 760px)", 1)[1]
        self.assertIn("position: static", mobile)
        self.assertIn("min-width: 0", mobile)
        self.assertIn("repeat(2, minmax(0, 1fr))", mobile)

    def test_current_resource_diagnoses_do_not_use_legacy_combined_pressure(self):
        self.assertNotIn("model.pressure", SOURCES["intelligence.js"])
        self.assertIn("state.intelligence?.health", SOURCES["app.js"])
        self.assertNotIn("state.health.cpu", SOURCES["app.js"])
        self.assertNotIn("state.health.memory_pressure", SOURCES["app.js"])


@unittest.skipUnless(shutil.which("node"), "Node unavailable; behavioral JS contracts not run")
class UIJavaScriptContractTests(unittest.TestCase):
    def run_js(self, code):
        result = subprocess.run([shutil.which("node"), "-e",
                                 "const assert = require('node:assert/strict');\n"
                                 "(async () => {\n" + code +
                                 "\n})().catch(error => { console.error(error); process.exitCode = 1; });"],
                                text=True, capture_output=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_high_commit_low_physical_usage_is_not_labeled_low_pressure(self):
        self.run_js("""
const nodes = new Map();
const $ = id => {if (!nodes.has(id)) nodes.set(id, {textContent: '', style: {}, dataset: {}}); return nodes.get(id);};
const fmt = number => Number(number).toFixed(1);
const bytes = number => number == null ? 'Unavailable' : `${number} B`;
const timeLabel = value => value;
const now = new Date().toISOString();
const state = {mode: 'live', settings: {enabled: true},
  latest: {observed_at: now, cpu_percent: 5, memory_total_bytes: 100, memory_available_bytes: 90,
    memory_commit_total_bytes: 96, memory_commit_limit_bytes: 100, memory_low_signal: false},
  health: {cpu: {status: 'high'}, memory_pressure: 'normal'},
  intelligence: {pressure: 'Low', health: {observed_at: now, evaluated_at: now, age_seconds: 0,
    freshness: 'fresh', cpu: {percent: 5, status: 'normal'}, physical: {used_percent: 10, status: 'normal'},
    commit: {used_percent: 96, status: 'high'}, os_low_memory: {signal: false, status: 'normal'},
    memory_pressure: 'high', disk: {status: 'unknown'}}}};
""" + "\n".join(js_function("app.js", name) for name in
                         ["counter", "physicalUsage", "renderMemory", "resourceStatus", "healthFreshnessText", "renderResourceHealth"]) + """
renderMemory(state.latest);
renderResourceHealth(state);
assert.equal($('memory').textContent, '10.0%');
assert.equal($('health-cpu').textContent, 'Normal');
assert.equal($('health-physical').textContent, 'Normal');
assert.equal($('health-commit').textContent, 'High');
assert.equal($('health-memory-pressure').textContent, 'High');
assert.match($('memory-pressure-explanation').textContent, /Memory pressure: High/);
assert.match($('memory-pressure-explanation').textContent, /96.0%/);
assert.equal($('memory-physical-status').textContent, 'Current diagnosis: Normal');
assert.equal($('memory-commit-status').textContent, 'Current diagnosis: High');
assert.match($('health-observed-at').textContent, /Observed/);
assert.match($('health-evaluated-at').textContent, /Evaluated/);
assert.doesNotMatch($('health-freshness').textContent, /Observed|Evaluated/);
""")

    def test_stale_cpu_values_are_historical_not_a_current_high_status(self):
        self.run_js("""
const nodes = new Map();
const $ = id => {if (!nodes.has(id)) nodes.set(id, {textContent: '', style: {}, dataset: {}}); return nodes.get(id);};
const fmt = number => Number(number).toFixed(1);
const bytes = number => number == null ? 'Unavailable' : `${number} B`;
const timeLabel = value => value;
const observed = new Date(Date.now() - 120000).toISOString();
const state = {mode: 'live', settings: {enabled: false}, latest: {observed_at: observed, cpu_percent: 99},
  health: {freshness: 'fresh', cpu: {status: 'high'}}, intelligence: {pressure: 'High', health: {
    observed_at: observed, evaluated_at: new Date().toISOString(), age_seconds: 120, freshness: 'stale',
    cpu: {percent: 99, status: 'unknown'}, physical: {status: 'unknown'}, commit: {status: 'unknown'},
    os_low_memory: {status: 'unknown'}, memory_pressure: 'unknown', disk: {status: 'unknown'}}}};
""" + "\n".join(js_function("app.js", name) for name in
                         ["counter", "resourceStatus", "healthFreshnessText", "renderResourceHealth"]) + """
renderResourceHealth(state);
assert.equal($('cpu').textContent, '99.0%');
for (const id of ['health-cpu', 'health-physical', 'health-commit', 'health-os-low-memory', 'health-memory-pressure', 'health-disk']) {
  assert.equal($(id).textContent, 'Unknown', id);
}
assert.match($('health-freshness').textContent, /Stale/);
assert.match($('health-observed-at').textContent, new RegExp(observed));
assert.match($('memory-pressure-explanation').textContent, /Current memory pressure is unknown/);
// Even a malformed cached status must not survive stale/unavailable updates.
state.intelligence.health.cpu.status = 'high';
renderResourceHealth(state); assert.equal($('health-cpu').textContent, 'Unknown');
state.intelligence.health.freshness = 'fresh';
renderResourceHealth(state, false); assert.equal($('health-cpu').textContent, 'Unknown');
assert.match($('health-freshness').textContent, /Updates unavailable/);
state.latest = null; state.intelligence.health = null;
renderResourceHealth(state);
assert.equal($('cpu').textContent, 'Unavailable');
assert.match($('health-freshness').textContent, /No samples/);
assert.match($('health-observed-at').textContent, /No sample timestamp/);
""")

    def test_basic_heuristic_cleanup_never_proposes_or_applies_permanent_delete(self):
        self.run_js("""
let uiMode = 'basic', currentSettings = {allow_permanent_delete: true};
let pendingFileReview = {file: {id: 1, name: 'important.txt', insights: {recommended_action: 'cleanup'}}};
const location = {hash: ''}, messages = [], calls = [];
const setFeedback = message => messages.push(message);
const $ = () => ({open: true, close() {}});
const post = async (...args) => {calls.push(args); throw Error('Heuristics must not call an actuator');};
const proposeFileAction = (...args) => {calls.push(args); throw Error('Heuristics must not propose permanent delete');};
""" + js_function("app.js", "permanentDeleteAvailable")
            + "\nconst expertView = () => uiMode === 'expert';\n"
            + js_function("files.js", "finishFileReviewDecision")
            + "\n" + js_function("files.js", "useAutomaticFileDecision") + """
assert.equal(permanentDeleteAvailable(), false);
await useAutomaticFileDecision();
assert.equal(location.hash, 'cleanup');
assert.equal(calls.length, 0);
assert.equal(pendingFileReview, null);
assert.match(messages.at(-1), /No file was deleted/);
pendingFileReview = {file: {id: 1, name: 'important.txt'}};
finishFileReviewDecision('delete');
assert.equal(calls.length, 0);
assert.match(messages.at(-1), /separately enabled Expert/);
""")

    def test_null_counters_are_unavailable_not_zero_and_memory_is_not_boosted(self):
        functions = "\n".join(js_function("app.js", name)
                              for name in ["counter", "physicalUsage", "renderMemory", "resourceStatus", "healthFreshnessText", "renderResourceHealth"])
        self.run_js("""
const nodes = new Map();
const $ = id => { if (!nodes.has(id)) nodes.set(id, {style: {}, dataset: {}, textContent: ''}); return nodes.get(id); };
const fmt = number => Number(number).toFixed(1);
const bytes = number => number == null ? 'Unavailable' : `${number} B`;
const timeLabel = value => value;
const currentMode = 'live';
""" + functions + """
const renderObservedMemory = (latest, enabled, health = null) => {
  renderMemory(latest);
  renderResourceHealth({latest, settings: {enabled}, mode: currentMode, intelligence: {health}});
};
assert.equal(counter(null), null);
assert.equal(counter('0'), null);
assert.equal(counter(-1), null);
assert.equal(physicalUsage({memory_total_bytes: 0, memory_available_bytes: 0}), null);
assert.equal(physicalUsage({memory_total_bytes: 100, memory_available_bytes: 110}), null);
renderObservedMemory(null, false);
for (const id of ['memory', 'memory-physical', 'memory-available', 'memory-cache', 'memory-low-signal']) {
  assert.equal($(id).textContent, 'Unavailable', id);
}
renderObservedMemory({memory_total_bytes: 100, memory_available_bytes: 25,
  observed_at: new Date().toISOString(),
  memory_commit_total_bytes: null, memory_commit_limit_bytes: null,
  memory_system_cache_bytes: 40, memory_low_signal: false}, true);
assert.equal($('memory').textContent, '75.0%');
assert.equal($('memory-available').textContent, '25 B');
assert.equal($('memory-commit').textContent, 'Unavailable / Unavailable');
assert.match($('memory-cache').textContent, /overlap|not additional free RAM/);
assert.equal($('memory-low-signal').textContent, 'No low-memory signal reported');
renderObservedMemory({memory_total_bytes: 100, memory_available_bytes: 25,
  observed_at: new Date().toISOString(),
  memory_commit_total_bytes: 95, memory_commit_limit_bytes: 100,
  memory_low_signal: null}, true, {freshness: 'fresh', age_seconds: 0,
    physical: {status: 'elevated'}, commit: {status: 'high', used_percent: 95},
    os_low_memory: {status: 'unknown'}, memory_pressure: 'high'});
assert.match($('memory-pressure-explanation').textContent, /Memory pressure: High/);
assert.match($('memory-pressure-explanation').textContent, /95.0%/);
assert.equal($('memory-low-signal').textContent, 'Unavailable');
renderObservedMemory({memory_low_signal: true, observed_at: new Date().toISOString()}, false,
  {freshness: 'fresh', os_low_memory: {status: 'high', signal: true}, memory_pressure: 'high'});
assert.match($('memory-pressure-explanation').textContent, /OS reports a low-memory signal/);
renderObservedMemory({memory_low_signal: true, observed_at: new Date(Date.now() - 60000).toISOString()}, true,
  {freshness: 'stale', age_seconds: 60, os_low_memory: {status: 'unknown'}, memory_pressure: 'unknown'});
assert.match($('memory-pressure-explanation').textContent, /Current memory pressure is unknown/);
renderObservedMemory({memory_low_signal: true}, true);
assert.match($('memory-pressure-explanation').textContent, /Current memory pressure is unknown/);
""")

    def test_priority_and_maintenance_approval_send_boolean_consent(self):
        priority = "$('confirm-trial').addEventListener" + js_between(
            "intelligence.js", "$('confirm-trial').addEventListener", "$('cancel-trial').addEventListener")
        maintenance = "$('maintenance-confirm').addEventListener" + SOURCES["maintenance.js"].split(
            "$('maintenance-confirm').addEventListener", 1)[1]
        self.run_js("""
const handlers = {}, nodes = {}, calls = [];
const $ = id => nodes[id] ||= {addEventListener: (kind, fn) => handlers[id] = fn, close() {}, disabled: false};
let pendingProposal = {id: 'trial'}, maintenancePlan = {id: 'tool', action: 'temp_cleanup'}, expert = true;
const expertView = () => expert;
const mutate = async (path, payload) => {calls.push([path, payload]); return {};};
const post = async (path, payload) => {calls.push([path, payload]); return {decision: {result: {message: 'Recorded'}, status: 'unknown'}};};
const refresh = async () => {};
const setFeedback = () => {};
""" + priority + maintenance + """
await handlers['confirm-trial']();
await handlers['maintenance-confirm']();
assert.deepEqual(calls.map(([path, payload]) => [path, payload.confirm]), [
  ['/api/actions/approve', true], ['/api/maintenance/execute', true]
]);
expert = false; pendingProposal = {id: 'not-approved'}; maintenancePlan = {id: 'trim', action: 'trim_memory'};
await handlers['confirm-trial'](); await handlers['maintenance-confirm']();
assert.equal(calls.length, 2);
""")

    def test_history_deletion_cancel_sends_nothing_and_approval_is_explicit(self):
        handler = "$('delete-button').addEventListener" + js_between(
            "app.js", "$('delete-button').addEventListener", "$('quit-button').addEventListener")
        self.run_js("""
let approved = false, click;
const calls = [];
const $ = () => ({addEventListener: (kind, fn) => click = fn});
const window = {confirm: () => approved};
const post = async (path, payload) => calls.push([path, payload]);
const refresh = async () => {};
const setFeedback = () => {};
""" + handler + """
await click(); assert.equal(calls.length, 0);
approved = true; await click();
assert.deepEqual(calls, [['/api/delete', {confirm: true}]]);
""")

    def test_file_move_apply_and_permanent_delete_fail_closed(self):
        self.run_js("""
const calls = [];
let approval = false, deletionEnabled = false;
const window = {confirm: () => approval, prompt: () => 'Reviewed/file.txt'};
const permanentDeleteAvailable = () => deletionEnabled;
const setFeedback = () => {};
const refreshFiles = async () => {};
const post = async (path, payload) => {
  calls.push([path, payload]);
  return path.endsWith('/propose') ? {source_relative: 'file.txt', destination_relative: 'Reviewed/file.txt', proposal_id: 'reviewed'}
    : {status: payload.proposal_id && calls.some(([p,b]) => p.endsWith('/propose') && b.kind === 'DELETE') ? 'deleted' : 'applied'};
};
""" + js_function("files.js", "proposeFileAction") + """
const file = {id: 7, name: 'file.txt', relative_path: 'file.txt'};
await proposeFileAction(file, 'MOVE');
assert.equal(calls.filter(([p]) => p.endsWith('/apply')).length, 0);
calls.length = 0; approval = true;
await proposeFileAction(file, 'MOVE');
assert.deepEqual(calls.find(([p]) => p.endsWith('/apply'))[1], {proposal_id: 'reviewed', confirm: true});
calls.length = 0;
await proposeFileAction(file, 'DELETE'); assert.equal(calls.length, 0);
deletionEnabled = true; approval = false;
await proposeFileAction(file, 'DELETE'); assert.equal(calls.length, 0);
approval = true; await proposeFileAction(file, 'DELETE');
assert.equal(calls.find(([p]) => p.endsWith('/apply'))[1].confirm, true);
""")

    def test_sort_undo_requires_approval_and_binds_the_reviewed_batch(self):
        self.run_js("""
let currentSortPlan = {id: 'reviewed'}, sortBusy = false, filesRevision = 0, approval = false, swap = false;
const calls = [], feedback = {style: {}};
const $ = () => feedback;
const setSortScopeControlsDisabled = () => {};
const renderSortPlan = () => {};
const refreshFiles = async () => {};
const requestSortApproval = async () => {if (swap) currentSortPlan = {id: 'unreviewed'}; return approval;};
const post = async (path, payload) => {calls.push([path,payload]); return {plan: {id: 'reviewed'}, rolled_back: [], failed: []};};
""" + js_function("files.js", "undoSortPlan") + """
await undoSortPlan(); assert.equal(calls.length, 0);
approval = true; swap = true;
await undoSortPlan(); assert.equal(calls.length, 0);
swap = false; currentSortPlan = {id: 'reviewed'};
await undoSortPlan();
assert.deepEqual(calls, [['/api/files/sort/undo', {plan_id: 'reviewed', confirm: true}]]);
""")

    def test_journal_apply_and_undo_each_require_their_own_review(self):
        self.run_js("""
let approval = false, controls = [];
const calls = [];
const element = () => ({dataset: {}, childNodes: [], append(...children) {this.childNodes.push(...children);}});
const journal = {replaceChildren() {}, append() {}};
const $ = () => journal;
const button = (text, action) => {const control = {text, action}; controls.push(control); return control;};
const timeLabel = value => value;
const permanentDeleteAvailable = () => false;
const requestSortApproval = async () => approval;
const setFeedback = () => {};
const refreshFiles = async () => {};
const post = async (path, payload) => {calls.push([path,payload]); return {status: path.endsWith('/undo') ? 'rolled_back' : 'applied'};};
""" + js_function("files.js", "renderActionJournal") + """
const action = {id: 1, proposal_id: 'exact-proposal', kind: 'MOVE', source_relative: 'a.txt', destination_relative: 'folder/a.txt'};
renderActionJournal([{...action, status: 'proposed'}]);
await controls.find(control => control.text === 'Apply').action();
assert.equal(calls.length, 0);
approval = true;
await controls.find(control => control.text === 'Apply').action();
assert.deepEqual(calls[0], ['/api/files/actions/apply', {proposal_id: 'exact-proposal', confirm: true}]);
controls = []; approval = false;
renderActionJournal([{...action, status: 'applied'}]);
await controls.find(control => control.text === 'Undo').action();
assert.equal(calls.length, 1);
approval = true;
await controls.find(control => control.text === 'Undo').action();
assert.deepEqual(calls[1], ['/api/files/actions/undo', {action_id: 1, confirm: true}]);
controls = [];
renderActionJournal([{...action, kind: 'DELETE', status: 'proposed'}]);
assert.equal(controls.some(control => control.text === 'Apply'), false);
""")

    def test_assistant_maintenance_and_process_execute_use_explicit_consent(self):
        handler = "$('chat-action-confirm').addEventListener" + js_between(
            "chat.js", "$('chat-action-confirm').addEventListener", "document.addEventListener('kshetrajna-state'")
        self.run_js("""
const calls = [], nodes = {};
let click, chatPlan = {type: 'maintenance', plan: {id: 'reviewed-tool', action: 'temp_cleanup'}};
const $ = id => nodes[id] ||= {value: 'app.exe', addEventListener: (kind, fn) => click = fn, close() {}};
const chatSession = 'local-session';
const expertView = () => false;
const chatMessage = () => {};
const refresh = async () => {};
const post = async (path,payload) => {calls.push([path,payload]); return {decision: {title: 'Observed outcome', result: {message: 'Request recorded'}}};};
""" + handler + """
await click();
assert.equal(calls[0][0], '/api/maintenance/execute'); assert.equal(calls[0][1].confirm, true);
chatPlan = {type: 'process', plan: {id: 'one-process', action: 'close'}};
await click();
assert.equal(calls[1][0], '/api/processes/execute'); assert.equal(calls[1][1].confirm, true);
chatPlan = {type: 'process', plan: {id: 'force', action: 'terminate'}};
await click(); assert.equal(calls.length, 2);
""")

    def test_chart_keeps_missing_values_as_gaps_and_has_a_text_summary(self):
        self.run_js("""
const element = () => ({attributes: {}, children: [], setAttribute(name, value) {this.attributes[name] = value;},
  append(child) {this.children.push(child);}, replaceChildren() {this.children = [];}});
const chart = element(), summary = element();
const $ = id => id === 'chart' ? chart : summary;
const document = {createElement: element, createElementNS: element};
const fmt = number => Number(number).toFixed(1);
""" + "\n".join(js_function("app.js", name) for name in
                         ["counter", "physicalUsage", "drawChart"]) + """
drawChart([
  {cpu_percent: 10, memory_total_bytes: 100, memory_available_bytes: 50},
  {cpu_percent: null, memory_total_bytes: null, memory_available_bytes: null},
  {cpu_percent: 20, memory_total_bytes: 100, memory_available_bytes: 25}
]);
assert.match(summary.textContent, /1 unavailable/);
assert.match(summary.textContent, /Gaps are not interpolated/);
const svg = chart.children[0];
assert.equal(svg.attributes['aria-hidden'], 'true');
assert.equal(svg.children.length, 4);
assert.ok(svg.children.every(line => !line.attributes.points.includes(' ')));
""")

    def test_preview_escape_cancels_without_opening_the_action_dialog(self):
        handler = "$('file-review-dialog').addEventListener('cancel'," + js_between(
            "files.js", "$('file-review-dialog').addEventListener('cancel',", "const cancelFilePreview")
        self.run_js("""
let escape, released = 0, closed = 0, pendingFileReview = {file: {id: 1}};
const $ = () => ({addEventListener: (kind, fn) => escape = fn, close: () => closed++});
const releaseFilePreview = () => released++;
const closeFileReviewAndChooseAction = () => {throw Error('Escape must not open another dialog');};
""" + handler + """
escape({preventDefault() {}});
assert.equal(pendingFileReview, null);
assert.equal(released, 1); assert.equal(closed, 1);
""")

    def test_polling_focus_restores_same_key_not_another_rows_button(self):
        self.run_js("""
let focused = null;
const body = {dataset: {}};
const node = (key, parent) => ({dataset: {focusKey: key}, parentElement: parent, isConnected: true,
  getClientRects: () => [1], closest: () => null, focus() {focused = this;}});
const list = {id: 'roots-list', parentElement: body};
const first = node('root:1', list), second = node('root:2', list);
const oldButton = node('button:Rescan', first);
const wrongButton = node('button:Rescan', second);
const newButton = node('button:Rescan', first);
const fallback = node('heading', body);
const $ = () => fallback;
const document = {body, activeElement: oldButton, querySelectorAll: () => [wrongButton, newButton]};
""" + "\n".join(js_function("app.js", name) for name in
                         ["focusIdentity", "captureFocus", "restoreFocus", "preserveFocus"]) + """
preserveFocus(() => {oldButton.isConnected = false; document.activeElement = body;});
assert.equal(focused, newButton);
const saved = {node: oldButton, key: focusIdentity(oldButton)};
newButton.isConnected = false;
document.querySelectorAll = () => [wrongButton];
restoreFocus(saved);
assert.equal(focused, fallback);
""")

    def test_unconfirmed_restore_does_not_claim_the_file_is_still_quarantined(self):
        self.run_js(js_function("files.js", "classifyQuarantineRestoreError") + """
const outcome = classifyQuarantineRestoreError(new Error('Network connection lost'));
assert.equal(outcome.state, 'unknown');
assert.match(outcome.message, /unconfirmed/);
assert.doesNotMatch(outcome.message, /file remains in quarantine/);
assert.match(outcome.message, /before another approval/);
""")

    def test_navigation_tracks_back_forward_and_legacy_hashes(self):
        self.run_js("""
const nodes = {}, names = ['overview', 'cleanup', 'memory', 'sort-ai', 'history', 'privacy', 'chat'];
const $ = id => nodes[id] ||= {focus() {}, scrollIntoView() {}};
const viewCopy = Object.fromEntries(names.map(name => [name, ['', name, name]]));
const pageSurfaces = Object.fromEntries(names.map(name => [name, {}]));
const links = names.map(name => ({dataset: {view: name}, classList: {toggle() {}},
  setAttribute(key,value) {this[key] = value;}, removeAttribute(key) {delete this[key];}}));
const document = {body: {dataset: {}}, querySelectorAll: selector => selector === '.side-nav a' ? links : []};
const location = {hash: '#memory'};
const window = {scrollTo() {}};
const requestAnimationFrame = callback => callback();
const reducedMotion = {matches: true};
let activeWorkspace = null;
""" + js_function("workspace.js", "switchWorkspace") + """
switchWorkspace(); assert.equal(document.body.dataset.view, 'memory');
assert.equal(links.find(link => link['aria-current']).dataset.view, 'memory');
location.hash = '#history'; switchWorkspace();
location.hash = '#memory'; switchWorkspace();
assert.equal(document.body.dataset.view, 'memory');
assert.equal(links.filter(link => link['aria-current']).length, 1);
location.hash = '#files'; switchWorkspace();
assert.equal(document.body.dataset.view, 'sort-ai');
assert.equal(links.find(link => link['aria-current']).dataset.view, 'sort-ai');
location.hash = '#main-content'; switchWorkspace();
assert.equal(document.body.dataset.view, 'sort-ai');
""")


if __name__ == "__main__":
    unittest.main()
