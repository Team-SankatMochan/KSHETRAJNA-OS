"use strict";

// Entirely synthetic, in-memory presentation. No backend or local-agent calls.
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);
  const messages = $("#messages");
  const dialog = $("#review-dialog");
  let pending = null;
  let revision = 0;
  let sorted = false;
  let trimmed = false;
  let cleaned = false;
  let closed = false;
  let actions = [];
  let scanState = "idle"; // idle | permission | scanning | complete

  // ── Helpers ──
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function message(text, role = "assistant") {
    const block = el("article", undefined, `message ${role}`);
    block.append(el("div", role === "user" ? "YOU" : "KSHETRAJNA · SCRIPTED DEMO", "speaker"));
    block.append(el("p", text));
    messages.append(block);
    while (messages.children.length > 60) messages.firstElementChild.remove();
    messages.scrollTop = messages.scrollHeight;
    return block;
  }

  // ── Operations ──
  const operations = {
    trim: {
      title: "Trim one background app's working set",
      detail: "Sample target: Discord · PID 2048 · 450 MB working set.\nPreview: request Windows EmptyWorkingSet for this single eligible background app.",
      risk: "Real trimming has no exact undo and the app may reload pages from disk. This demo changes only a sample reading.",
      control: "#allow-maintenance"
    },
    cleanup: {
      title: "Delete reviewed temporary files",
      detail: "Sample preview: 3 old files · 82 MB · fixed temporary folder.\nNo Downloads, documents, browser data or active files are included.",
      risk: "Real temporary-file deletion is permanent. Review the live file list first. Here, no files are accessed or deleted.",
      control: "#allow-maintenance"
    },
    close: {
      title: "Request graceful closure of Discord",
      detail: "Sample target: Discord · PID 2048 · background for 15 minutes.\nA real request sends WM_CLOSE to this app's visible windows. It does not force-kill it.",
      risk: "Low CPU does not prove an app is safe to close. Unsaved work can be lost. Closure has no undo and may be refused by the app.",
      control: "#allow-close"
    },
    sort: {
      title: "Move files into suggested project & category folders",
      detail: "Full scan results: 38,247 indexes scanned\n\n385 files matched for sorting:\n• 197 files → project folders (Aarthika, NaviiGo, Kshetrajna-OS, SankatMochan, College-Notes, Hackathon-2026, Photography)\n• 188 files → category folders (Documents, Images, Code, Media, Archives, Sheets, Design, Misc)\n\n37,862 files verified in correct locations — no changes needed.\n\nThe live app reviews conflicts and checks file identity before applying.",
      risk: "These example moves are simulated. The real app requires approved folder access, an explicit plan approval per batch, and records supported undo. Files are never moved without your explicit batch approval.",
      control: "#allow-sort"
    },
    undo: {
      title: "Undo the sample file organization",
      detail: "Restore all 385 sorted files to their original locations.\nProject files return to their source directories.\nCategory-sorted files return to Downloads.",
      risk: "Live undo checks file identity and destination conflicts. This demo restores only the sample plan.",
      control: "#allow-sort"
    }
  };

  function done(kind) {
    return kind === "trim" ? trimmed : kind === "cleanup" ? cleaned : kind === "close" ? closed : kind === "sort" ? sorted : !sorted;
  }

  function card(parent, kind) {
    const op = operations[kind];
    const panel = el("section", undefined, "action-card");
    panel.append(el("h3", op.title));
    panel.append(el("p", op.detail));
    const btn = el("button", "Review simulation");
    btn.type = "button";
    btn.addEventListener("click", () => {
      if (done(kind)) { message("This sample action is already complete. Reset the demo to try again."); return; }
      if (!$(op.control).checked) { message("Enable the matching action in Demo controls before reviewing."); $(op.control).focus(); return; }
      pending = { kind, revision };
      $("#review-title").textContent = op.title;
      $("#review-detail").textContent = op.detail;
      $("#review-risk").textContent = op.risk;
      dialog.showModal();
    });
    panel.append(btn, el("small", "Sample target only. A separate approval is required."));
    parent.append(panel);
    messages.scrollTop = messages.scrollHeight;
  }

  // ── Journal ──
  function updateJournal() {
    const journal = $("#journal");
    journal.replaceChildren();
    if (!actions.length) journal.append(el("li", "No actions yet. Every approval is recorded here."));
    actions.slice(-8).reverse().forEach((entry) => journal.append(el("li", entry)));
    const used = trimmed ? "10.35" : "10.8";
    const memory = $("#memory");
    memory.replaceChildren(document.createTextNode(used), el("span", " / 16 GB", "metric-unit"));
    $("#memory-bar").style.setProperty("--fill", `${(Number(used) / 16 * 100).toFixed(1)}%`);
  }

  // ══════════════════════════════════════════════
  //  SORT AI — Full Scan Simulation
  // ══════════════════════════════════════════════

  const scanPaths = [
    "C:\\Users\\parra\\Desktop\\",
    "C:\\Users\\parra\\Downloads\\",
    "C:\\Users\\parra\\Documents\\",
    "C:\\Users\\parra\\Pictures\\",
    "C:\\Users\\parra\\Videos\\",
    "C:\\Users\\parra\\OneDrive\\Desktop\\NaviiGo-App\\",
    "C:\\Users\\parra\\OneDrive\\Desktop\\KshetRajna-OS\\",
    "D:\\Projects\\Aarthika\\",
    "D:\\Projects\\Aarthika\\lib\\",
    "D:\\Projects\\Aarthika\\assets\\",
    "C:\\Users\\parra\\Documents\\College-Notes\\",
    "C:\\Users\\parra\\Documents\\College-Notes\\DSA\\",
    "C:\\Users\\parra\\Documents\\College-Notes\\OS\\",
    "C:\\Users\\parra\\Pictures\\Photography\\2026\\",
    "C:\\Users\\parra\\Pictures\\Screenshots\\",
    "C:\\Users\\parra\\OneDrive\\Documents\\",
    "C:\\Users\\parra\\OneDrive\\Desktop\\Hackathon-2026\\",
    "C:\\Users\\parra\\Downloads\\Installers\\",
    "C:\\Users\\parra\\Desktop\\temp-files\\",
    "C:\\Users\\parra\\Videos\\Recordings\\",
    "C:\\Users\\parra\\Music\\",
    "D:\\Backups\\",
    "C:\\Users\\parra\\AppData\\Local\\Temp\\",
    "C:\\Users\\parra\\Documents\\Work\\",
    "Finalizing index analysis...",
    "Building project clusters...",
    "Matching files to destinations...",
    "Verifying existing folder locations...",
    "Scan complete — generating report..."
  ];

  const SCAN_TOTAL = 38247;
  const SCAN_SORTED = 385;
  const SCAN_PROJECTS = 7;
  const SCAN_UNCHANGED = SCAN_TOTAL - SCAN_SORTED;

  function animateCounter(element, target, duration = 2000) {
    const start = performance.now();
    const initial = parseInt(element.textContent.replace(/,/g, "")) || 0;
    function update(now) {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
      const current = Math.round(initial + (target - initial) * eased);
      element.textContent = current.toLocaleString();
      if (progress < 1) requestAnimationFrame(update);
    }
    requestAnimationFrame(update);
  }

  function showPermission() {
    scanState = "permission";
    $("#scan-permission").style.display = "";
    $("#scan-progress").style.display = "none";
    $("#scan-results").style.display = "none";
    // Scroll to Sort AI
    $("#sort-ai-section").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function startScan() {
    scanState = "scanning";
    $("#scan-permission").style.display = "none";
    $("#scan-progress").style.display = "";
    $("#scan-results").style.display = "none";
    
    const scanBtn = $("#start-scan");
    scanBtn.textContent = "Scanning...";
    scanBtn.classList.add("scanning");
    scanBtn.disabled = true;
    
    let step = 0;
    const totalSteps = 100;
    const pathIndex = { current: 0 };
    
    function tick() {
      step++;
      const progress = step / totalSteps;
      const percentNum = Math.min(Math.round(progress * 100), 100);
      
      // Update progress bar
      $("#scan-fill").style.width = `${percentNum}%`;
      $("#scan-percent").textContent = `${percentNum}%`;
      
      // Update live stats with easing
      const indexed = Math.round(SCAN_TOTAL * Math.min(progress * 1.1, 1));
      const categorized = Math.round(SCAN_SORTED * Math.min(progress * 1.2, 1));
      const projects = Math.round(SCAN_PROJECTS * Math.min(progress * 1.5, 1));
      const unchanged = Math.round(SCAN_UNCHANGED * Math.min(progress * 1.05, 1));
      
      $("#live-indexed").textContent = indexed.toLocaleString();
      $("#live-categorized").textContent = categorized.toLocaleString();
      $("#live-projects").textContent = projects.toLocaleString();
      $("#live-unchanged").textContent = unchanged.toLocaleString();
      
      // Update status text
      if (progress < 0.3) {
        $("#scan-status-text").textContent = "Indexing filesystem...";
      } else if (progress < 0.5) {
        $("#scan-status-text").textContent = "Analyzing file metadata...";
      } else if (progress < 0.65) {
        $("#scan-status-text").textContent = "Detecting project clusters...";
      } else if (progress < 0.8) {
        $("#scan-status-text").textContent = "Categorizing files by type...";
      } else if (progress < 0.9) {
        $("#scan-status-text").textContent = "Matching destinations...";
      } else {
        $("#scan-status-text").textContent = "Finalizing sort plan...";
      }
      
      // Update scanning path
      const pathIdx = Math.min(Math.floor(progress * scanPaths.length), scanPaths.length - 1);
      if (pathIdx !== pathIndex.current) {
        pathIndex.current = pathIdx;
        const pathEl = $("#scan-current-path");
        pathEl.textContent = scanPaths[pathIdx];
      }
      
      if (step < totalSteps) {
        // Variable speed — slower at start, faster towards end
        const delay = progress < 0.3 ? 80 : progress < 0.7 ? 60 : 40;
        setTimeout(tick, delay);
      } else {
        completeScan();
      }
    }
    
    setTimeout(tick, 500);
  }

  function completeScan() {
    scanState = "complete";
    
    // Hide progress, show results
    setTimeout(() => {
      $("#scan-progress").style.display = "none";
      $("#scan-results").style.display = "";
      
      const scanBtn = $("#start-scan");
      scanBtn.textContent = "✓ Scan Complete";
      scanBtn.classList.remove("scanning");
      scanBtn.disabled = true;
      scanBtn.style.background = "rgba(52,211,153,0.15)";
      scanBtn.style.color = "#34d399";
      scanBtn.style.boxShadow = "none";
      
      // Animate result numbers
      setTimeout(() => {
        // Animate the category bars
        $$(".category-fill").forEach(bar => {
          bar.style.width = bar.style.getPropertyValue("--fill");
        });
      }, 300);
      
      // Add to journal
      actions.push("SORT AI: Full scan completed — 38,247 indexes scanned, 385 files matched for organization, 7 projects detected");
      updateJournal();
      
      // Add message
      message("✅ Sort AI scan complete!\n\n📊 38,247 indexes scanned across your entire filesystem\n✅ 385 files identified for auto-sorting\n📌 37,862 files already in correct locations\n🎯 7 project clusters detected\n\nScroll up to the Sort AI panel to review categories, projects, and approve batches. Each batch can be individually reviewed and approved.");
    }, 600);
  }

  // ── Scan UI Handlers ──
  $("#start-scan").addEventListener("click", () => {
    if (scanState === "idle") showPermission();
    else if (scanState === "complete") message("The scan is already complete. Reset the demo to run another scan.");
  });

  $("#approve-scan").addEventListener("click", () => {
    startScan();
    message("📍 Scan approved! Indexing your filesystem...\n\nScope: Desktop, Downloads, Documents, Pictures, Videos, OneDrive, D:\\Projects\nSystem folders and protected paths are automatically excluded.\n\nYou'll see live progress above. Every file movement requires your explicit approval.");
  });

  $("#deny-scan").addEventListener("click", () => {
    $("#scan-permission").style.display = "none";
    scanState = "idle";
    message("Scan cancelled. You can launch it any time from the Sort AI panel.");
  });

  // ── Tab Switching ──
  $$(".breakdown-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      $$(".breakdown-tab").forEach(t => t.classList.remove("active"));
      $$(".breakdown-content").forEach(c => c.classList.remove("active"));
      tab.classList.add("active");
      $(`#tab-${tab.dataset.tab}`).classList.add("active");
    });
  });

  // ── Approval Buttons ──
  $$(".approve-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      if (!$("#allow-sort").checked) {
        message("Enable 'File organization' in Demo controls before approving batches.");
        $("#allow-sort").focus();
        return;
      }
      const action = btn.dataset.action;
      btn.textContent = "✓ Approved";
      btn.classList.add("approved");
      btn.disabled = true;
      
      let result;
      if (action === "approve-projects") {
        result = "SIMULATED: 197 project files moved to their respective project folders (Aarthika: 23, NaviiGo: 31, Kshetrajna-OS: 18, SankatMochan: 14, College-Notes: 42, Hackathon-2026: 11, Photography: 58)";
      } else if (action === "approve-categories") {
        result = "SIMULATED: 188 files organized by category (87 docs, 104 images, 62 code, 43 media, 28 archives, 34 sheets, 18 design, 9 misc)";
      } else if (action === "approve-folders") {
        result = "SIMULATED: Created Projects\\SankatMochan\\ and Projects\\Hackathon-2026\\";
      }
      
      if (result) {
        actions.push(result);
        updateJournal();
        message(result);
        sorted = true;
      }
    });
  });

  // ── Chat Response Engine ──
  function respond(text) {
    const q = text.toLowerCase();
    
    if (/privacy|permission|control/.test(q)) {
      message("This web demo reads no PC telemetry, files or local AI service. Messages and simulated approvals stay in tab memory and disappear on reset or reload. Hosting providers may keep ordinary page-access logs.\n\nIn the Windows app, telemetry and file indexing require consent. Local AI, maintenance, app closure and priority trials have separate controls. Each change requires review and approval. Reversal is supported where possible; deletion and process closure are not reversible.");
    } else if (/undo|reverse|restore/.test(q)) {
      if (sorted) card(message("The sample sorting plan has recorded original locations for all 385 files. Review the undo before restoring."), "undo");
      else message("There is no active sample file move to undo. Memory trimming, temporary-file deletion and closing an app have no exact undo.");
    } else if (/scan|sort|organiz|download|file/.test(q) && !/temp|clean/.test(q)) {
      if (scanState === "complete") {
        if (sorted) message("All sample files are already organized. Ask 'undo sorting' to review restoration.\n\n📊 38,247 indexes scanned\n✅ 385 files sorted into projects & categories\n📌 37,862 files unchanged\n🎯 7 projects detected");
        else card(message("Sort AI has completed scanning 38,247 file indexes.\n\n✅ 385 files ready for organization:\n• 197 files matched to 7 project clusters\n• 188 files categorized by type\n• 37,862 files already in correct locations\n\nProject detection found: Aarthika (23 files), NaviiGo (31), Kshetrajna-OS (18), SankatMochan (14), College-Notes (42), Hackathon-2026 (11), Photography (58)\n\nReview the plan above and approve each batch, or use the action card below."), "sort");
      } else if (scanState === "scanning") {
        message("A scan is currently in progress. Watch the live stats above for real-time progress.");
      } else {
        showPermission();
        message("Sort AI is ready to perform a full-laptop scan. I'll ask permission to access your filesystem directories.\n\nThe scan covers Desktop, Downloads, Documents, Pictures, Videos, OneDrive, and project directories. System folders are excluded. Every file move requires your explicit approval.");
      }
    } else if (/project|aarthika|navigo|sankat|college|hackathon|photo/i.test(q)) {
      if (scanState === "complete") {
        message("🎯 Project Detection Results:\n\n1. Aarthika — 23 files (Finance App · Flutter)\n   → D:\\Projects\\Aarthika\\ (existing folder found)\n\n2. NaviiGo — 31 files (Travel App · Flutter)\n   → Desktop\\NaviiGo-App\\ (existing folder found)\n\n3. Kshetrajna-OS — 18 files (Desktop Agent · Python)\n   → Desktop\\KshetRajna-OS\\ (existing folder found)\n\n4. SankatMochan — 14 files (Team Assets · Mixed)\n   → Projects\\SankatMochan\\ (new folder proposed)\n\n5. College-Notes — 42 files (Academic · Documents)\n   → Documents\\College-Notes\\ (existing folder found)\n\n6. Hackathon-2026 — 11 files (Competition · Mixed)\n   → Projects\\Hackathon-2026\\ (new folder proposed)\n\n7. Photography — 58 files (Personal · Images)\n   → Pictures\\Photography\\ (existing folder found)\n\nSort AI detects projects by analyzing filename prefixes, folder patterns, and file type clusters. It looks for existing project folders across your entire filesystem before proposing new ones.");
      } else {
        message("Run the Sort AI scan first to detect projects. Click 'Launch Full Scan' above or say 'launch sort AI scan'.\n\nSort AI detects projects by analyzing filename patterns (like 'aarthika_', 'navigo_'), shared prefixes, and file type clusters. It searches your entire laptop for existing project folders before creating new ones.");
      }
    } else if (/temp|clean|cache/.test(q)) {
      if (cleaned) message("The sample temporary-file cleanup has already been completed. Storage cleanup frees disk space, not RAM.");
      else card(message("Temporary files use storage, not RAM. The real app previews old files in a fixed temporary folder before deletion. Windows-managed memory caches are not automatically purged."), "cleanup");
    } else if (/idle|kill|close|process|background/.test(q)) {
      if (closed) message("A sample graceful-close request was already recorded.");
      else card(message("Sample candidate: Discord, with low CPU in a recent background window. Low activity alone does not establish that it is safe to terminate. The live app checks an allowlist, process identity, owner, session and foreground status."), "close");
    } else if (/ram|memory|free/.test(q)) {
      if (trimmed) message("The sample working-set trim is complete. The updated number is illustrative.");
      else card(message("The sample system uses 10.8 of 16 GB RAM. I can show a targeted working-set trim for one background app. In the live app, the review uses current data and explicitly enabled maintenance controls."), "trim");
    } else if (/predict|workflow|pattern|routine/.test(q)) {
      message("📊 Workflow Analysis (Sample Data)\n\nDetected pattern: Editor → Browser → Terminal\n\nPredicted next app: Browser (78% confidence)\n\nSession breakdown:\n• VS Code — 4.2h active today\n• Chrome — 2.8h active today\n• Windows Terminal — 1.5h active today\n• Discord — 0.3h (background)\n\nThe live predictor uses ordered routines, recency-weighted transitions and session boundaries. It can abstain when evidence is weak and evaluates predictions against historical replay.");
    } else if (/overview|cpu|pc|status|stat/.test(q)) {
      message("📊 Workspace Overview (Sample Data)\n\n💻 Development workflow active\n• CPU: 24% · developing workload\n• Memory: " + (trimmed ? "10.35" : "10.8") + " / 16 GB\n• Active apps: VS Code, Chrome, Terminal, Discord\n\n" + (scanState === "complete" ? "📁 Sort AI: 38,247 indexes scanned · 385 files sorted · 7 projects detected\n\n" : "") + "Try: 'launch sort AI scan', 'free RAM', 'review idle apps', 'predict workflow', or 'clean temp files'.");
    } else if (/hello|hi |hey|greet|what can/.test(q)) {
      message("👋 Hello! I'm Kshetrajna — your workspace intelligence.\n\nHere's what I can help with:\n\n🔍 Sort AI — Full laptop file scanning with project detection\n💾 Memory — Trim working sets and free RAM\n🚀 Workflows — Predict your next app and analyze patterns\n🧹 Maintenance — Review and clean temporary files\n🔒 Privacy — Explain data collection and controls\n\nTry 'launch sort AI scan' to see the full file organization experience!");
    } else {
      message("I'm the scripted web demo. Try these commands:\n\n🔍 'launch sort AI scan' — Full filesystem scan with project detection\n📊 'show projects' — View detected project clusters\n💾 'free RAM' — Review memory optimization\n👀 'review idle apps' — Check background processes\n📁 'organize downloads' — Smart file categorization\n🧹 'clean temp files' — Temporary file cleanup\n🔮 'predict workflow' — Workflow pattern analysis\n↩️ 'undo sorting' — Reverse file moves\n🔒 'privacy' — Data handling explanation\n\nFor AI conversation, run the Windows app with a local Ollama model.");
    }
  }

  function send(text) {
    text = String(text).trim().slice(0, 2000);
    if (!text) return;
    message(text, "user");
    respond(text);
  }

  // ── Event Wiring ──
  $("#chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    send($("#prompt").value);
    $("#prompt").value = "";
  });

  $$("[data-prompt]").forEach((button) =>
    button.addEventListener("click", () => {
      if (button.classList.contains("nav-item")) {
        $$(".nav-item").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        // Update breadcrumb
        const label = button.querySelector(".nav-label");
        if (label) {
          $("#header-breadcrumb").textContent = `YOUR WORKSPACE / ${label.textContent.toUpperCase()}`;
        }
      }
      send(button.dataset.prompt);
      $("#prompt").focus();
    })
  );

  // ── Dialog ──
  $("#cancel-review").addEventListener("click", () => { pending = null; dialog.close(); });
  dialog.addEventListener("cancel", () => { pending = null; });
  $("#approve-review").addEventListener("click", () => {
    const plan = pending;
    pending = null;
    dialog.close();
    if (!plan || plan.revision !== revision || done(plan.kind) || !$(operations[plan.kind].control).checked) {
      message("The sample plan is stale or its action permission was disabled. Nothing changed.");
      return;
    }
    let result;
    if (plan.kind === "trim") {
      trimmed = true;
      result = "SIMULATED: one working-set trim. Example RAM reading: 10.35 / 16 GB. This is not a measurement of your PC.";
    }
    if (plan.kind === "cleanup") {
      cleaned = true;
      result = "SIMULATED: 3 old temporary files removed · 82 MB of example disk space. No real files were touched.";
    }
    if (plan.kind === "close") {
      closed = true;
      result = "SIMULATED: graceful-close request sent for Discord. App exit is not verified. No real process was touched.";
    }
    if (plan.kind === "sort") {
      sorted = true;
      result = "SIMULATED: 385 files organized — 197 to project folders, 188 by category. 7 project clusters detected. Original locations recorded. Ask 'undo sorting' to review restoration.";
    }
    if (plan.kind === "undo") {
      sorted = false;
      // Reset approval buttons
      $$(".approve-btn").forEach(btn => {
        btn.textContent = "Review & Approve";
        btn.classList.remove("approved");
        btn.disabled = false;
      });
      result = "SIMULATED: 385 files restored to their original locations across all project and category batches.";
    }
    revision += 1;
    actions.push(result);
    updateJournal();
    message(result);
  });

  // ── Mobile Menu ──
  const mobileToggle = $("#mobile-toggle");
  const sidebar = $("#sidebar");
  if (mobileToggle && sidebar) {
    mobileToggle.addEventListener("click", () => {
      sidebar.classList.toggle("open");
    });
    // Close sidebar when clicking a nav item on mobile
    $$(".nav-item").forEach(item => {
      item.addEventListener("click", () => {
        if (window.innerWidth <= 850) {
          sidebar.classList.remove("open");
        }
      });
    });
  }

  // ── Reset ──
  function reset() {
    pending = null;
    if (dialog.open) dialog.close();
    revision += 1;
    sorted = trimmed = cleaned = closed = false;
    scanState = "idle";
    actions = [];
    $("#prompt").value = "";
    $$(".controls-card input").forEach((input) => { input.checked = false; });
    $$(".nav-item").forEach((btn, i) => btn.classList.toggle("active", i === 0));
    messages.replaceChildren();
    updateJournal();
    
    // Reset Sort AI
    $("#scan-permission").style.display = "none";
    $("#scan-progress").style.display = "none";
    $("#scan-results").style.display = "none";
    const scanBtn = $("#start-scan");
    scanBtn.innerHTML = '<span class="scan-icon">⟳</span> Launch Full Scan';
    scanBtn.classList.remove("scanning");
    scanBtn.disabled = false;
    scanBtn.style.background = "";
    scanBtn.style.color = "";
    scanBtn.style.boxShadow = "";
    
    // Reset approval buttons
    $$(".approve-btn").forEach(btn => {
      btn.textContent = "Review & Approve";
      btn.classList.remove("approved");
      btn.disabled = false;
    });
    
    // Reset progress
    $("#scan-fill").style.width = "0%";
    $("#scan-percent").textContent = "0%";
    $("#live-indexed").textContent = "0";
    $("#live-categorized").textContent = "0";
    $("#live-projects").textContent = "0";
    $("#live-unchanged").textContent = "0";
    
    // Reset tabs
    $$(".breakdown-tab").forEach((t, i) => t.classList.toggle("active", i === 0));
    $$(".breakdown-content").forEach((c, i) => c.classList.toggle("active", i === 0));
    
    // Reset breadcrumb
    $("#header-breadcrumb").textContent = "YOUR WORKSPACE / OVERVIEW";
    
    message("Your workspace, with you in control.\n\nExplore the review-and-approve flow using sample memory, apps and files. Try the new Sort AI with full-laptop scanning and project detection.\n\nThis demo cannot access your computer. For real observations and local AI conversation, start the Windows app.");
  }

  $("#reset").addEventListener("click", reset);
  reset();
})();
