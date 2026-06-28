# IT Support Assistant

A desktop chatbot I built for handling first-line IT support tickets — account lockouts, VPN issues, printer problems, that kind of thing. It's a single Python file using Tkinter for the UI, and it can talk to a local Ollama model, Claude, or DeepSeek depending on what you've got set up.

Author: Juan Baez

## Why this exists

Most "AI helpdesk" demos just describe troubleshooting steps in plain text. I wanted something that could actually do things — look up a ticket, check whether an account is locked, pull in a relevant internal procedure — while still being something you can run on your own machine without a server or a subscription.

## What it does

- Talks to **Ollama** by default (free, local, no API key), or you can switch to **Claude** or **DeepSeek** in Settings.
- Streams responses token by token, same as a normal chat app.
- Can call real tools when tool use is enabled: check account status, unlock an account, look up a ticket in a local SQLite database, search the web, or pull from an internal knowledge base of IT procedures. There's also an opt-in for running real (read-only, allowlisted) diagnostic commands on your own machine — ping, ipconfig, dns lookups, that sort of thing.
- Supports attaching a screenshot — Claude and a handful of vision-capable Ollama models (llava, moondream, gemma3, etc.) can actually read it.
- Multiple tabs, each one its own independent ticket/conversation, all of it saved locally and reloaded next time you open the app.
- Dark/light theme, exportable chat transcripts, a sidebar with quick-action shortcuts for common issues.
- Packageable into a single `.exe` with PyInstaller if you want to hand it to someone without making them install Python.

## Getting it running

You'll need Python 3.9+ (Tkinter ships with it on most installers).

```
pip install -r requirements.txt
python it_support_chatbot.py
```

By default it's set up to use Ollama. Install it from ollama.com, run `ollama serve`, and pull a model:

```
ollama pull gemma3
```

If you'd rather use Claude or DeepSeek instead, open Settings (the gear icon, or Ctrl+,), switch the provider, and paste in your API key — each provider's key is stored separately so switching back and forth doesn't clobber anything.

## A few things worth knowing

**Ollama capability detection** is just a heuristic based on model name, since Ollama doesn't expose an API for "does this model support tools/vision." If a model you know supports one of those isn't being detected, it's just missing from the `OLLAMA_MODEL_CAPABILITIES` dict in the code — easy to add.

**Tool use** works with Claude and with Ollama models that match a known tool-capable pattern. The account/ticket tools are backed by a simulated fake directory and a real local SQLite database respectively — wiring the account tools up to a real Active Directory or ticketing system would just mean swapping out the bodies of a few functions, the tool-calling plumbing around them doesn't need to change.

**Local diagnostics** are off by default and have to be turned on separately from general tool use, since it's the one feature that actually runs commands on your machine. It's strictly allowlisted (ping, dns lookup, network config, etc.) and any argument that comes from the model is validated against a strict pattern before it touches a subprocess call — no shell, no arbitrary commands.

**Web search and the knowledge base** are both lightweight context injection rather than a full agent loop — relevant results get appended to your message before it's sent, just for that one turn, and never written into the saved conversation history.

**Image attachments** get downscaled and sent as either a Claude content block or an Ollama `images` field depending on which provider's active — switching providers after attaching an image (before sending it) just drops the attachment instead of sending the wrong format.

## Packaging as a standalone .exe

```
pip install pyinstaller
pyinstaller --onefile --windowed --name "IT-Support-Assistant" --clean it_support_chatbot.py
```

That produces `dist\IT-Support-Assistant.exe`, about 17MB, no console window, no Python required on the machine you hand it to. There's an `IT-Support-Assistant.spec` file included so you can just re-run `pyinstaller IT-Support-Assistant.spec` after future code changes instead of retyping the flags.

One thing to watch for: the app writes its config/history/database files next to wherever it's actually running from — the script's folder when run as `.py`, or the folder containing the `.exe` when frozen. Keep the `.exe` in its own folder since it'll create a few files alongside itself on first run.

## File layout

```
it_support_chatbot.py        main application
requirements.txt             dependencies
README.md                    this file
IT-Support-Assistant.spec    PyInstaller build spec
```

Local files created at runtime (not checked into git): `.it_support_config.json` (settings/keys), `.it_support_history.json` (saved conversations), `knowledge_base.json` (KB articles), `tickets.db` (ticket database).
