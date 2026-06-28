# AI-Powered IT Support Desktop Application

**Stack:** Python · Claude (Anthropic) / DeepSeek / local Ollama · Tkinter GUI  
**Author:** Juan Baez

---

## Features

| Feature | Detail |
|---|---|
| Multi-session tabs | Work multiple tickets in parallel — each tab is an independent conversation with its own history, ticket, and web-search toggle |
| Tool use (Claude + Ollama) | The assistant can actually check/act on things — account lockout status, password reset, real ticket lookups, real local diagnostics — instead of only describing steps |
| Image attachments (Claude + Ollama vision) | Attach a screenshot of an error/BSOD and the model reads it directly |
| Standalone .exe | Packaged with PyInstaller for one-file, no-Python-required deployment to non-technical users |
| Conversational AI | **Ollama (local, default) · Claude · DeepSeek** — pick a provider in Settings, answers stream in real time, token-by-token |
| Friendlier UI | Color-coded provider badge, hover tooltips on every icon button, keyboard shortcuts (Ctrl+N/W/Tab/,), sectioned Settings dialog |
| Internal knowledge base | 14 built-in IT procedure articles (lockouts, VPN, DNS, printers, BitLocker, etc.); automatically referenced when relevant; add your own via the sidebar |
| Live web search | Optional 🌐 toggle gives the assistant real internet context (DuckDuckGo Instant Answer + Wikipedia search), keyless |
| Persistent Memory | Full session history is sent with every request, enabling contextual follow-ups |
| Conversation persistence | Chat survives app restarts — reloaded from a local history file on launch |
| Quick Actions | 10 sidebar shortcuts for the most common IT issues |
| Threaded API calls | Non-blocking — UI stays responsive while streaming a response |
| Animated loader | Three-dot animation shown until the first streamed token arrives |
| Markdown rendering | **Bold**, `code`, headings, and bullet lists render formatted in chat bubbles |
| Robust error handling | Network failures · Rate limits · Malformed JSON · Auth errors · mid-stream errors |
| Real ticket system | Tickets are written to a local SQLite database (`tickets.db`), not just a random string |
| Ticket viewer | Sidebar panel lists all tickets; double-click to toggle open/resolved |
| Copyable chat text | All message bubbles are selectable / copyable |
| Saved API key | Key persists locally between launches — no need to re-enter it |
| Copy conversation / ticket | One-click clipboard copy of the full transcript or the last ticket number |
| Export chat | Save the transcript to a `.txt` or `.json` file |
| Dark / light theme | Toggle with the header button; preference is remembered |
| Settings panel | Choose model, temperature, and max tokens from the header ⚙️ button |

---

## Setup

### 1. Install Python 3.9+
Tkinter is included with most Python installers.  
Verify: `python --version`

### 2. Install dependencies
```
pip install -r requirements.txt
```

### 3. Choose a provider

**Default — Ollama (local, free, no API key, no internet required for chat):**
Install [Ollama](https://ollama.com), run `ollama serve`, then pull a model, e.g.:
```
ollama pull llama3.2
```
This is the default provider on first launch — nothing else to configure.

**Alternative — Claude (Anthropic API):**
Sign up at https://console.anthropic.com and copy your API key, then switch providers in Settings (see step 5).

**Alternative — DeepSeek API:**
Sign up at https://platform.deepseek.com and copy your API key, then switch providers in Settings (see step 5).

### 4. Run the app
```
python it_support_chatbot.py
```
Or run the prebuilt `dist\IT-Support-Assistant.exe` — see **Packaging as a standalone .exe** below.

### 5. (Optional) Switch provider or model
Click the **⚙️** button in the header (or press **Ctrl+,**) to pick **Ollama**, **Claude**, or **DeepSeek** and choose a model.  
If you switch to Claude or DeepSeek, also paste that provider's API key in the header bar and click **Set Key** — each provider's key is remembered separately, so switching back and forth never loses one.

---

## Usage

- **➕ New Ticket / ✕ Close Tab** (above the chat, or **Ctrl+N** / **Ctrl+W**) — open or close parallel conversations. Each tab is a fully independent ticket: its own history, ticket number, and web-search toggle.
- **Ctrl+Tab** / **Ctrl+Shift+Tab** to cycle tabs, **Ctrl+1/2/3** to jump straight to the 1st/2nd/3rd tab.
- **Sidebar quick-actions** — click any button to instantly submit a pre-written IT prompt into the active tab.  
- **Type your issue** in the input box and press **Enter** (use **Shift+Enter** for a new line).  
- The assistant remembers the full conversation per tab — you can ask follow-up questions naturally.  
- Replies stream in live, token-by-token, rendered with basic markdown (bold, code, headings, bullets).  
- The colored badge in the header (green/red/purple) always shows which provider and model are active at a glance — hover it for a reminder of where to change it.  
- Hover any icon-only button (☀️/🌙, ⚙️) for a tooltip explaining what it does.  
- Click **Copy Conversation** to copy the active tab's transcript to your clipboard.  
- Click **Copy Last Ticket** to copy the active tab's most recent escalation ticket number.  
- Click **Export Chat** to save the active tab's transcript as a `.txt` or `.json` file.  
- Click **View All Tickets** to open a list of every ticket across all tabs; double-click a row to toggle open/resolved.  
- Click **Knowledge Base** to browse the built-in IT articles, or add your own (title, tags, content) — the assistant will start referencing them on the next message.  
- Click the **⚙️** button in the header (or **Ctrl+,**) to switch provider (Ollama / Claude / DeepSeek), change model, temperature, max tokens, or toggle tool use — now organized into clear Provider / Generation / Tools sections.  
- Toggle **🌐 Web Search** (above the input box, per tab) to give the assistant live internet context for your next message — useful for current error codes, recent CVEs, version-specific quirks, etc. Works with any provider.  
- Click the **☀️/🌙** button in the header to switch between dark and light themes.  
- Click **Clear This Ticket** to wipe the active tab's history and start fresh.

Your provider/API keys/theme/model settings are saved to a local `.it_support_config.json` file; all open tabs' conversations are saved to `.it_support_history.json` and reloaded automatically next launch (each tab restores as its own tab). Tickets live in `tickets.db` (SQLite) next to the script.

### About Ollama mode
- No API key, no per-token cost, and chat itself works fully offline once a model is pulled.
- The model list in Settings is auto-populated from your local `ollama list`; if Ollama isn't running you can still type a model name manually.
- A cold (just-started) local model can take 30–90+ seconds to load before the first token streams back — this is normal, not a bug.
- **Tool use and image attachment now work with Ollama too**, for models that actually support them — see **About tool use** and **About image attachments** below. The default model was changed from `llama3.2` to `llama3.1` specifically because llama3.2's base text models don't reliably support tool calling.
- Since Ollama has no API to ask "does this model support tools/vision", capability detection is a heuristic based on the model name (`llama3.1`, `qwen2.5`, `mistral`, `gemma`, etc. → tools; `llama3.2-vision`, `llava`, `qwen2-vl`, `gemma3`, `moondream` → vision). Settings shows what was detected for your selected model. If a model you know supports one of these isn't detected, it just means the name pattern isn't in `OLLAMA_MODEL_CAPABILITIES` yet — add it there.
- `ollama pull moondream` is a good way to try image attachment locally — it's small (~1.7 GB) and pulls/loads fast, and was used to verify this feature actually works end-to-end (not just code-complete) — confirmed it correctly read an error code and identified shapes/colors in real test images.

### About DeepSeek mode
- Uses DeepSeek's OpenAI-compatible chat completions API with streaming — same live token-by-token feel as Claude and Ollama.
- Two models available in Settings: `deepseek-chat` (general purpose) and `deepseek-reasoner` (extended reasoning).
- Needs an API key from https://platform.deepseek.com, set independently of your Claude key — switching providers in Settings never overwrites or loses the other provider's saved key.
- Tool use and image attachment aren't wired up for DeepSeek yet (Claude and capable Ollama models only) — see **About tool use** below.

### About Web Search
- Uses two free, keyless sources: DuckDuckGo's Instant Answer API (good for factual/topic lookups) and Wikipedia's search API (broader keyword coverage).
- This is lightweight context-injection, not a tool-calling agent loop — results are appended to your message before it's sent to the model, for that turn only. It is not stored in conversation history, so it won't pollute future turns.
- It's not a full search engine — don't expect it to find today's breaking news or live error-code databases, but it materially helps with anything Wikipedia or DuckDuckGo's knowledge graph covers.

### About the internal knowledge base
- Stored in `knowledge_base.json` next to the script, seeded with 14 starter articles on first run (account lockouts, password resets, MFA, VPN, Wi-Fi, DNS, Outlook, slow PCs, printers, file permissions, software crashes, Windows Update, software install policy, BitLocker).
- Retrieval is simple keyword overlap (no embeddings/vector DB needed at this scale) — it matches on words shared between your message and each article's title/tags/content, always on, no toggle needed.
- This is also context-injection like web search: matches are appended to your message for that one request only, never written to persisted history.
- Add your own articles via **Knowledge Base** in the sidebar, or edit `knowledge_base.json` directly — this is the easiest way to make the assistant "smarter" about your specific environment (internal tool names, non-standard procedures, ticketing system quirks, etc.).

### About multi-session tabs
- Each tab is a fully independent `ChatSession` — its own message history, ticket number, and web-search toggle. The provider/model/theme settings are shared app-wide (changing them in Settings applies to every tab).
- Tab titles auto-update: starts as "New Ticket", becomes a short preview of your first message, then switches to the actual ticket number (`TKT-XXXXX`) once one is generated for that conversation.
- All open tabs persist across restarts and reopen exactly as you left them.
- Closing the last remaining tab opens a fresh one automatically — you're never left with zero tabs.

### About tool use (Claude + capable Ollama models)
- When enabled (Settings → "Enable tool use"), the model can call real tools instead of only describing manual steps: `check_account_status` / `unlock_account` / `trigger_password_reset` (a **simulated demo Active Directory**, not a real company directory — deterministic per-username fake data, consistent for the rest of the conversation), `check_ticket_status` (queries the **real** local `tickets.db`), `search_the_web` / `fetch_webpage`, `search_internal_kb`, and — if separately opted into — `run_diagnostic` (real read-only commands on your actual machine, see below).
- Works with Claude, and with Ollama for models whose name matches a known tool-capable pattern (`llama3.1`+, `qwen2.5`, `mistral`, `gemma`, `command-r`, etc.) — confirmed working end-to-end against a real local Ollama model during development, including the AD-unlock flow. DeepSeek tool use isn't wired up yet.
- Mechanically, each provider has its own tool-call wire format under the hood (Anthropic's content blocks vs. Ollama's OpenAI-style `tool_calls`/`role: tool` messages) but the same Python tool functions and the same result either way: the app executes the matching function, feeds the result back, and the model continues — up to 4 rounds per message. Only the final clean text (no tool-call internals) is saved to conversation history, the same ephemeral-context pattern used for web search and the knowledge base.
- To wire the AD/ticket tools up to a real Active Directory or ticketing system, replace the bodies of `tool_check_account_status`, `tool_unlock_account`, and `tool_trigger_password_reset` in `it_support_chatbot.py` with real LDAP/Graph API calls — the tool-calling plumbing around them doesn't need to change.

### About real local diagnostics
- A separate, **off-by-default** opt-in (Settings → "Allow real local diagnostics") since it's the one tool that actually executes commands on your machine — gated independently of the general tool-use toggle.
- Strictly allowlisted and read-only: `network_config`, `dns_lookup`, `ping`, `traceroute`, `system_info`, `service_status`, `group_policy_result`, `disk_space`, `active_connections`. No arbitrary command execution — the model can only select one of these named actions, and any `target` argument (hostname/IP/service name) is validated against a strict pattern before being placed in a non-shell argument list, so there's no command-injection surface.
- Windows only for now (`ipconfig`, `systeminfo`, `gpresult`, etc. are Windows-specific commands).

### About image attachments (Claude + vision-capable Ollama models)
- Click the 📎 button to attach a screenshot (error dialog, BSOD, confusing UI state) — the model reads it directly instead of you describing it in words.
- Works with Claude, and with Ollama for models whose name matches a known vision-capable pattern (`llama3.2-vision`, `llava`, `qwen2-vl`, `gemma3`, `moondream`). The attach button is disabled with an explanatory tooltip otherwise.
- Requires Pillow (`pip install Pillow`, already in `requirements.txt`) — without it the button is disabled with a tooltip explaining why.
- Large images are downscaled to a max 1568px dimension before sending (Anthropic's own recommended ceiling). The attached image survives app restarts (persisted in `.it_support_history.json`), but is provider-specific under the hood: Claude gets an image content block, Ollama gets a sibling `images` field — switching providers after attaching (before sending) safely drops the attachment rather than sending the wrong format.

## Packaging as a standalone .exe

For deploying to non-technical users who shouldn't need to install Python:

```
pip install pyinstaller
pyinstaller --onefile --windowed --name "IT-Support-Assistant" --clean it_support_chatbot.py
```

This produces `dist\IT-Support-Assistant.exe` (~17 MB, single file, no console window). Hand that one file to a user — no Python, no pip install required on their machine.

**Important:** the app stores its config/history/knowledge-base/tickets files next to wherever it's running from. For the `.py` script that's the script's folder; for the packaged `.exe` it's the folder containing the `.exe` (not a temp folder — this is handled automatically via `sys.executable` when frozen, so data survives between launches). Put the `.exe` in its own folder before handing it out, since it'll create 3-4 files alongside itself on first run.

A `IT-Support-Assistant.spec` file is included for reproducible rebuilds — re-run `pyinstaller IT-Support-Assistant.spec` after code changes instead of re-specifying the flags.

---

## Error handling matrix

| Condition | Behaviour |
|---|---|
| No internet (Claude provider) | "Network Error" message shown; failed user message rolled back from history |
| Ollama not running locally | Clear message telling you to run `ollama serve` |
| Ollama model not pulled | Clear message telling you to run `ollama pull <model>` |
| Server timeout | Friendly timeout message; can retry immediately |
| HTTP 429 rate limit (Claude) | Tells user how long to wait (Retry-After header) |
| HTTP 401 bad key (Claude / DeepSeek) | Clear "Invalid API key" prompt naming the active provider |
| HTTP 429 rate limit (DeepSeek) | Friendly "rate limit reached" message |
| Missing API key (Claude / DeepSeek) | Warns before sending, names the provider whose key is missing |
| Malformed JSON / mid-stream error event | Caught and surfaced; any partial streamed bubble is discarded |
| Web search unreachable | Silently returns no results — chat still proceeds normally |
| Tool execution fails (Claude) | Error message fed back to the model as the tool result so it can recover/explain, rather than crashing the turn |
| Any other exception | Catch-all safety net; writes a new ticket to `tickets.db` |

---

## File structure

```
it_support_chatbot.py        ← main application (single file)
requirements.txt             ← pip dependencies
README.md                    ← this file
IT-Support-Assistant.spec    ← PyInstaller build spec (for reproducible .exe rebuilds)
dist\IT-Support-Assistant.exe ← packaged standalone build (after running PyInstaller)
.it_support_config.json      ← saved API keys (per provider), theme, model settings (created on first use)
.it_support_history.json     ← saved tabs/conversations, reloaded on next launch
knowledge_base.json          ← internal IT articles (seeded with defaults on first run)
tickets.db                   ← SQLite database of all escalation tickets (all tabs)
```
