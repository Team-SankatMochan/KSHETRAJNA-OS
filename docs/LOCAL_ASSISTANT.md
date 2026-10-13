# Local chat and reviewed PC tools

Kshetrajna includes a conversational interface backed by an installed local
Ollama model. It requires no paid API key, cloud account, or automatic model
download. It is not OpenAI ChatGPT, and response quality/speed depend on the
selected model and hardware.

## Start

1. Start Ollama on this computer. This development machine already has
   `llama3.1:8b` installed. Other devices need their own compatible runtime and
   model. Install from https://ollama.com/download if needed; choosing and
   downloading a model remains a user action.
2. Start Ollama separately and open **Launch.cmd** from a Windows bundle.
   For a source checkout, `run_chat.cmd` can start an already installed Ollama
   runtime; `run.cmd` also works when Ollama is already running.
3. Open **Chat**, choose an installed model, and enable local AI after reviewing
   its consent message. Model selection and the opt-in are saved locally.
4. Enable live collection to inspect this PC. Demo readings and actions remain
   synthetic even when the conversation uses a real model.
5. Enable **Windows maintenance** or **reviewed app controls** in Privacy to
   allow their corresponding review cards. Enabling a control never performs
   an action by itself.

## Conversation

Examples:

- `Explain what RAM does.`
- `Review my RAM usage and help me free up memory.`
- `Show idle background processes.`
- `Close pid 1234.`
- `Preview temporary file cleanup.`
- `Reset Microsoft Store cache.`
- `Help me organize my Downloads files.`
- `Unload the local model.`

Replies stream into the conversation. **Stop** cancels the local request.
Recorded outcomes of approved actions enter the chat history, so later replies
can distinguish a close request from a confirmed exit.
**New chat** clears the conversation and pending replies from Kshetrajna memory.
No chat database is created. Histories are bounded to 16 recent messages,
conversations expire after 30 minutes of inactivity, and all disappear when
the application stops. System-history deletion also clears chats.

Local system tools remain available with AI disabled. Their replies are clearly
identified as **local tools** rather than presented as model-generated text.

## Actions

The model cannot run shell commands or invoke arbitrary tools. A deterministic
controller interprets supported requests and produces review cards. Models
advertising tool support can also request the single `review_pc` function for
natural-language requests. That function accepts only an enumerated review
category and cannot execute changes, choose arbitrary PIDs, or invoke commands.
At most one review request and one follow-up response are performed per turn.
User text,
process names, and model output never serve as action approval. Review and
execute are separate token-protected endpoints.

Memory review offers eligible one-process working-set trims and Windows storage
tools. Available physical memory comes from fresh telemetry. Trimming resident
pages can cause page faults and slow an app, and is not a system-wide cache
flush, permanent RAM saving, or proven speedup. An **Unload model** control asks
Ollama to release the selected model's allocation; the next reply reloads it.
Inference uses a one-minute keep-alive timeout to avoid retaining that model
indefinitely. In-progress inference must finish or be stopped before unloading.

App review identifies **low-CPU background candidates** from at least three
observations spanning 30 seconds, with no recorded foreground use and bounded
gaps. CPU alone cannot establish that an app is unused: it might be waiting for
I/O or holding unsaved work. The view covers collected processes, not every
process on the machine. Missing/stale telemetry produces no eligible actions.

An explicit allowlist currently includes Chrome, Edge, Firefox, VS Code,
Notepad, Notepad++, Discord, Spotify, Steam, and VLC. System/security services,
Python, Ollama, and Kshetrajna are outside it. Each action targets one PID,
never a process tree or every idle process.

- **Request close** posts a normal close request to that PID's visible windows.
  Save prompts may appear. A close request is not proof the app exited.
- **Force terminate** requires a fresh preview, separate approval and typing
  the exact executable name. It may lose unsaved work and has no undo.
- The native driver checks the same Windows session and user SID, excludes
  critical processes and Windows system paths, verifies the executable and
  creation token, and protects foreground apps. Force termination operates
  through the verified process handle so PID reuse cannot retarget it.
- Approval expires after 60 seconds. A durable decision is recorded before
  mutation. Outcomes appear in the shared journal, and unknown interrupted
  operations are never replayed automatically.

Temporary cleanup, Store cache and Disk Cleanup keep their existing individual
previews, opt-ins and irreversible-operation disclosures. Organizing files
opens the reviewed Sort AI panel; chat does not silently scan folders.

## Local model boundary

Only `127.0.0.1:11434` is contacted by the provider. Installed models must pass
local model discovery and metadata checks. Cloud-labelled models and remote
providers are rejected. No proxy, arbitrary host, API key, model pull, or shell
execution API is exposed.

After local AI consent, the prompt includes the conversation and an available
fresh telemetry summary. No document excerpts, file contents or directory
listings are sent to the model. Collection pause prevents retained telemetry
from being included as a new PC summary. Previous chat messages remain in the
conversation until cleared. Ollama is a separate process on the same
computer and may have its own logs/configuration. Kshetrajna chat clearing does
not claim to erase memory or logs owned by that runtime.

## Snapdragon / ARM64

The Python client and process bindings use the project's standard-library and
pointer-sized Windows declarations. The source suite and packaged application
passed native Windows x64 and ARM64 CI. Local model replies were checked on x64.
The external Ollama runtime is a separate compatibility requirement: its
official Windows documentation currently describes AMD64 distribution. Verify
the runtime available on the Snapdragon device, or its emulation behavior,
before claiming native ARM64 inference. No Qualcomm NPU acceleration is claimed.

## Verification

The integration suite covers local-AI consent, retained-history privacy,
conversation history, clearing/cancellation, provider errors, cloud rejection,
HTTP session guards, process allowlists, foreground/identity/expiry checks,
force confirmation and journal-before-mutation ordering. Native user SID
bindings were also checked read-only on Windows. Tests use fixtures and driver
doubles; no personal app was closed or terminated during validation.

References: [Ollama chat API](https://docs.ollama.com/api/chat),
[Ollama on Windows](https://docs.ollama.com/windows),
[Windows TerminateProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess).
