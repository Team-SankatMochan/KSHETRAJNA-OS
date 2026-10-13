# Vercel deployment

The repository contains two intentionally separate applications:

- **Windows app:** `src/kshetrajna`, launched with `run.cmd` or `run_chat.cmd`.
  Real telemetry, SQLite history, approved file operations, process controls
  and optional installed Ollama inference run on the user's computer.
- **Hosted web demo:** `web-demo`, plain HTML/CSS/JavaScript with scripted chat,
  synthetic metrics, reviewed simulated actions, a session journal and file
  organization undo. No backend, model, API key or personal data is included.

## Deploy from GitHub

1. Import `Team-SankatMochan/KSHETRAJNA-OS` in Vercel.
2. Select **experimental/sort-ai-jarvis**, which contains this integration.
   If the integration is merged later, use the branch containing that merge.
   Vercel initially defaults to `main`. Change **Project Settings → Environments
   → Production → Branch Tracking** to `experimental/sort-ai-jarvis`, then
   create a deployment from that branch. You can also create a preview from
   this Git reference under the project's Deployments page. See the
   [official Git deployment guide](https://vercel.com/docs/git).
3. Set **Framework Preset: Other** and **Root Directory: repository root**
   (leave the directory field empty).
4. The committed root `vercel.json` sets an empty install command, an empty
   build command, and **Output Directory: web-demo**. No environment variables
   are needed. If the import screen shows overrides, use these same values.
5. Deploy. Open the resulting URL and try Free RAM → enable Memory & cleanup
   → Review simulation → Approve simulation. Try Sort files and then ask
   `undo sorting` to explore reversible file organization.

Only `web-demo` is served. Python source, submission files, local databases
and the Windows control API are not deployed as web endpoints. There are no
serverless functions, model downloads or network calls in the demo script.
Security headers prevent embedding and outgoing script connections.

Vercel supports static HTML/CSS/JavaScript without a build step; see the
[official build settings](https://vercel.com/docs/builds/configure-a-build).
The current Python server uses `ThreadingHTTPServer`, binds to `127.0.0.1`,
and relies on local Win32 and optional local Ollama. It is not a Vercel
ASGI/WSGI application. A hosted page cannot control a visitor's PC through
this implementation. Do not expose the Windows service with a tunnel or
disable its origin/token checks to make the hosted demo appear live.

## Run the real application

Clone/pull the repository, install Python 3.13 for your Windows architecture,
and run `run_chat.cmd`. For AI conversation, separately install Ollama and
an appropriate local model. Select the installed model and enable local AI
inside Chat. The launch script does not download models automatically.
Enable telemetry and the desired action controls in Privacy; review each
action locally. The hosted demo includes an **Open running local app** link
that navigates to the already running loopback dashboard; it does not call
that dashboard's API or transmit its data to the hosted site.

On Snapdragon, use native ARM64 Python and run `check_device.cmd
--require-native-arm64`. New model/inference performance needs device testing.
See [local assistant setup](LOCAL_ASSISTANT.md) and [ARM64 checks](SNAPDRAGON.md).

## Local web-demo preview

From the repository root:

```powershell
python -m http.server 8801 --bind 127.0.0.1 --directory web-demo
```

Open `http://127.0.0.1:8801`. This previews the static content; Vercel applies
the configured response headers when deployed. Messages and simulated actions
are kept only in tab memory and clear on reset or reload. Hosting providers
may retain ordinary access logs. No actual deployment or Vercel account
connection is implied by committing these files.
