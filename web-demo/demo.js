"use strict";

// Entirely synthetic, in-memory presentation. No backend or local-agent calls.
(() => {
  const $ = (selector) => document.querySelector(selector);
  const messages = $("#messages");
  const dialog = $("#review-dialog");
  let pending = null;
  let revision = 0;
  let sorted = false;
  let trimmed = false;
  let cleaned = false;
  let closed = false;
  let actions = [];

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function message(text, role = "assistant") {
    const block = element("article", undefined, `message ${role}`);
    block.append(element("div", role === "user" ? "YOU" : "KSHETRAJNA · SCRIPTED DEMO", "speaker"));
    block.append(element("p", text));
    messages.append(block);
    while (messages.children.length > 60) messages.firstElementChild.remove();
    messages.scrollTop = messages.scrollHeight;
    return block;
  }

  const operations = {
    trim: {title: "Trim one background app's working set", detail: "Sample target: Discord · PID 2048 · 450 MB working set.\nPreview: request Windows EmptyWorkingSet for this single eligible background app.", risk: "Real trimming has no exact undo and the app may reload pages from disk. This demo changes only a sample reading.", control: "#allow-maintenance"},
    cleanup: {title: "Delete reviewed temporary files", detail: "Sample preview: 3 old files · 82 MB · fixed temporary folder.\nNo Downloads, documents, browser data or active files are included.", risk: "Real temporary-file deletion is permanent. Review the live file list first. Here, no files are accessed or deleted.", control: "#allow-maintenance"},
    close: {title: "Request graceful closure of Discord", detail: "Sample target: Discord · PID 2048 · background for 15 minutes.\nA real request sends WM_CLOSE to this app's visible windows. It does not force-kill it.", risk: "Low CPU does not prove an app is safe to close. Unsaved work can be lost. Closure has no undo and may be refused by the app.", control: "#allow-close"},
    sort: {title: "Move two files into suggested folders", detail: "Sample Downloads plan:\nnotes.txt → Documents/notes.txt\nposter.png → Images/poster.png\nThe live app reviews conflicts and checks file identity before applying.", risk: "These example moves are simulated. The real app requires approved folder access, an explicit plan approval, and records supported undo.", control: "#allow-sort"},
    undo: {title: "Undo the sample file organization", detail: "Restore notes.txt and poster.png to their original sample Downloads locations.", risk: "Live undo checks file identity and destination conflicts. This demo restores only the sample plan.", control: "#allow-sort"}
  };

  function done(kind) {
    return kind === "trim" ? trimmed : kind === "cleanup" ? cleaned : kind === "close" ? closed : kind === "sort" ? sorted : !sorted;
  }

  function card(parent, kind) {
    const operation = operations[kind];
    const panel = element("section", undefined, "action-card");
    panel.append(element("h3", operation.title));
    panel.append(element("p", operation.detail));
    const button = element("button", "Review simulation");
    button.type = "button";
    button.addEventListener("click", () => {
      if (done(kind)) {message("This sample action is already complete or no longer applicable. Reset the demo to try again."); return;}
      if (!$(operation.control).checked) {message("Enable the matching action in Demo controls before reviewing it. Nothing has changed."); $(operation.control).focus(); return;}
      pending = {kind, revision};
      $("#review-title").textContent = operation.title;
      $("#review-detail").textContent = operation.detail;
      $("#review-risk").textContent = operation.risk;
      dialog.showModal();
    });
    panel.append(button, element("small", "Sample target only. A separate approval is required."));
    parent.append(panel);
    messages.scrollTop = messages.scrollHeight;
  }

  function updateJournal() {
    const journal = $("#journal");
    journal.replaceChildren();
    if (!actions.length) journal.append(element("li", "No actions yet. Every approval is recorded here."));
    actions.slice(-5).reverse().forEach((entry) => journal.append(element("li", entry)));
    const used = trimmed ? "10.35" : "10.8";
    const memory = $("#memory");
    memory.replaceChildren(document.createTextNode(used), element("span", " / 16 GB"));
    $("#memory-bar").value = Number(used);
  }

  function respond(text) {
    const query = text.toLowerCase();
    if (/privacy|permission|control/.test(query)) {
      message("This web demo reads no PC telemetry, files or local AI service. Messages and simulated approvals stay in tab memory and disappear on reset or reload. Hosting providers may keep ordinary page-access logs.\n\nIn the Windows app, telemetry and file indexing require consent. Local AI, maintenance, app closure and priority trials have separate controls. Each change requires review and approval. Reversal is supported where possible; deletion and process closure are not reversible.");
    } else if (/undo|reverse|restore/.test(query)) {
      if (sorted) card(message("The sample sorting plan has a recorded original location for each file. Review the undo before restoring it."), "undo");
      else message("There is no active sample file move to undo. Memory trimming, temporary-file deletion and closing an app have no exact undo; the demo never presents them as reversible.");
    } else if (/sort|organiz|download|file/.test(query) && !/temp|clean/.test(query)) {
      if (sorted) message("The two sample files are already organized. Ask 'undo sorting' to review restoration.");
      else card(message("I found two example Downloads files. The live Sort AI panel proposes categories using local metadata and bounded excerpts from approved folders. Here is a sample plan you can review."), "sort");
    } else if (/temp|clean|cache/.test(query)) {
      if (cleaned) message("The sample temporary-file cleanup has already been completed. Storage cleanup frees disk space, not RAM.");
      else card(message("Temporary files use storage, not RAM. The real app previews old files in a fixed temporary folder before deletion. Windows-managed memory caches are not automatically purged."), "cleanup");
    } else if (/idle|kill|close|process|background/.test(query)) {
      if (closed) message("A sample graceful-close request was already recorded. A close request does not establish that the app exited.");
      else card(message("Sample candidate: Discord, with low CPU in a recent background window. Low activity alone does not establish that it is safe to terminate. The live app checks an allowlist, process identity, owner, session and foreground status before offering a reviewed action."), "close");
    } else if (/ram|memory|free/.test(query)) {
      if (trimmed) message("The sample working-set trim is complete. The updated number is an illustrative reading, not a measured improvement on your computer.");
      else card(message("The sample system uses 10.8 of 16 GB RAM. I can show a targeted working-set trim for one background app. In the live app, the review uses current data and explicitly enabled maintenance controls."), "trim");
    } else if (/predict|workflow|pattern|routine/.test(query)) {
      message("Illustrative workflow: editor → browser → terminal. Example next-app suggestion: browser.\n\nThe live predictor uses ordered routines, recency-weighted transitions and session boundaries. It can abstain when evidence is weak and evaluates predictions against historical replay. These sample values are not your usage history or a prediction accuracy score.");
    } else if (/overview|cpu|pc|status/.test(query)) {
      message("Sample workspace: Development · CPU 24% · Memory 10.8 / 16 GB before any demo trim. Example apps: editor, browser, terminal and Discord.\n\nTry memory review, temporary-file cleanup, file sorting or workflow prediction. To work with your real data, run the Windows application and enable collection locally.");
    } else {
      message("I’m the scripted web demo. Try 'free RAM', 'review idle apps', 'organize Downloads', 'clean temporary files', 'predict my workflow', 'undo sorting' or 'privacy'.\n\nFor general AI conversation, run run_chat.cmd, select an installed Ollama model and enable local AI. No API key is needed; the model runs on your laptop.");
    }
  }

  function send(text) {
    text = String(text).trim().slice(0, 2000);
    if (!text) return;
    message(text, "user");
    respond(text);
  }

  $("#chat-form").addEventListener("submit", (event) => {event.preventDefault();send($("#prompt").value);$("#prompt").value = "";});
  document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => {
    if (button.classList.contains("nav-item")) {
      document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
    }
    send(button.dataset.prompt);
    $("#prompt").focus();
  }));
  $("#cancel-review").addEventListener("click", () => {pending = null;dialog.close();});
  dialog.addEventListener("cancel", () => {pending = null;});
  $("#approve-review").addEventListener("click", () => {
    const plan = pending;
    pending = null;
    dialog.close();
    if (!plan || plan.revision !== revision || done(plan.kind) || !$(operations[plan.kind].control).checked) {
      message("The sample plan is stale or its action permission was disabled. Nothing changed.");return;
    }
    let result;
    if (plan.kind === "trim") {trimmed = true;result = "SIMULATED: one working-set trim. Example RAM reading: 10.35 / 16 GB. This is not a measurement of your PC.";}
    if (plan.kind === "cleanup") {cleaned = true;result = "SIMULATED: 3 old temporary files removed · 82 MB of example disk space. No real files were touched.";}
    if (plan.kind === "close") {closed = true;result = "SIMULATED: graceful-close request sent for Discord. App exit is not verified. No real process was touched.";}
    if (plan.kind === "sort") {sorted = true;result = "SIMULATED: two files moved into suggested folders. Original locations recorded. Ask 'undo sorting' to review restoration.";}
    if (plan.kind === "undo") {sorted = false;result = "SIMULATED: two files restored to their original Downloads locations.";}
    revision += 1;
    actions.push(result);
    updateJournal();
    message(result);
  });
  function reset() {
    pending = null;
    if (dialog.open) dialog.close();
    revision += 1;
    sorted = trimmed = cleaned = closed = false;
    actions = [];
    $("#prompt").value = "";
    document.querySelectorAll(".controls input").forEach((input) => {input.checked = false;});
    document.querySelectorAll(".nav-item").forEach((button, index) => button.classList.toggle("active", index === 0));
    messages.replaceChildren();
    updateJournal();
    message("Your workspace, with you in control.\n\nExplore the review-and-approve flow using sample memory, apps and files. This demo cannot access your computer. For real observations and local AI conversation, start the Windows app.");
  }
  $("#reset").addEventListener("click", reset);
  reset();
})();
