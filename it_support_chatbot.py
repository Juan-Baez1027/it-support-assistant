"""
AI-Powered IT Support Desktop Application
Author: Juan Baez
Stack:  Python · Claude API (Anthropic) · Tkinter GUI

Features:
  - Conversational AI chatbot for automated IT helpdesk support
  - Persistent conversation memory (full session history sent each request)
  - Robust exception handling: network failures, rate limits, malformed JSON
  - Quick-action sidebar for the most common IT issues
  - Non-blocking API calls via threading
  - Animated loading indicator while waiting for AI
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont
import base64
import html
import io
import itertools
import json
import os
import platform
import re
import requests
import sqlite3
import subprocess
import sys
import threading
import random
import string
import uuid
from datetime import datetime, timedelta

try:
    from PIL import Image as PILImage, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ────────────────────────────────────────────────
#  Configuration / constants
# ────────────────────────────────────────────────
CLAUDE_URL   = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-6"

MODEL_OPTIONS = [
    "claude-sonnet-4-6",
    "claude-opus-4-8",
    "claude-haiku-4-5-20251001",
    "claude-fable-5",
]

DEEPSEEK_URL   = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL_OPTIONS = ["deepseek-chat", "deepseek-reasoner"]
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"

PROVIDERS = ["ollama", "claude", "deepseek"]
PROVIDER_LABELS = {"ollama": "Ollama", "claude": "Claude", "deepseek": "DeepSeek"}
PROVIDER_BADGE_COLOR = {"ollama": "#4caf50", "claude": "#e94560", "deepseek": "#7c5cff"}

OLLAMA_HOST     = "http://localhost:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_HOST}/api/chat"
OLLAMA_TAGS_URL = f"{OLLAMA_HOST}/api/tags"
# llama3.1 (not llama3.2) — it's the smallest widely-available Ollama model with
# solid, reliable tool-calling support, which matters now that tool use works
# cross-provider. llama3.2's base text models don't reliably support tools.
DEFAULT_OLLAMA_MODEL = "gemma4:latest"

# Best-effort capability detection by model-family name fragment — Ollama has no
# API to ask a model "do you support tools/vision", so this is a heuristic used
# to decide whether to offer tool use / image attachment for the loaded model.
OLLAMA_MODEL_CAPABILITIES = {
    "llama3.2-vision": {"vision"},
    "llama3.1":         {"tools"},
    "llama3.3":         {"tools"},
    "qwen2.5":          {"tools"},
    "qwen2-vl":         {"vision", "tools"},
    "mistral-nemo":     {"tools"},
    "mistral-small":    {"tools"},
    "mistral":          {"tools"},
    "firefunction":     {"tools"},
    "llava":            {"vision"},
    "moondream":        {"vision"},   # confirmed empirically — small, fast, genuinely reads images
    # Confirmed empirically against a real local Ollama instance — modern Gemma
    # builds support function calling, not just the vision-only label they're
    # often associated with.
    "gemma":            {"tools"},
    "gemma3":           {"vision", "tools"},
    "command-r":        {"tools"},
}


def get_ollama_model_capabilities(model_name: str) -> set:
    name = (model_name or "").lower()
    caps = set()
    for fragment, fragment_caps in OLLAMA_MODEL_CAPABILITIES.items():
        if fragment in name:
            caps |= fragment_caps
    return caps

# When frozen by PyInstaller, __file__ resolves to a temp extraction folder that's
# wiped between runs — use the .exe's own directory instead so config/history/db
# files actually persist across launches.
if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE  = os.path.join(_APP_DIR, ".it_support_config.json")
HISTORY_FILE = os.path.join(_APP_DIR, ".it_support_history.json")
TICKETS_DB   = os.path.join(_APP_DIR, "tickets.db")

TICKET_PATTERN = re.compile(r"TKT-\d{5}")


def load_config() -> dict:
    """Load saved settings (API key, theme, model params) from a local JSON file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(data: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def load_history() -> dict:
    """Load the persisted conversation (survives app restarts)."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_history(data: dict) -> None:
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ────────────────────────────────────────────────
#  Ticket store (SQLite)
# ────────────────────────────────────────────────
def init_tickets_db() -> None:
    conn = sqlite3.connect(TICKETS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_number TEXT PRIMARY KEY,
            created_at    TEXT NOT NULL,
            issue_summary TEXT,
            status        TEXT NOT NULL DEFAULT 'open'
        )
    """)
    conn.commit()
    conn.close()


def record_ticket(ticket_number: str, issue_summary: str) -> None:
    conn = sqlite3.connect(TICKETS_DB)
    conn.execute(
        "INSERT OR IGNORE INTO tickets (ticket_number, created_at, issue_summary, status) "
        "VALUES (?, ?, ?, 'open')",
        (ticket_number, datetime.now().isoformat(timespec="seconds"), issue_summary[:500]))
    conn.commit()
    conn.close()


def create_ticket(issue_summary: str) -> str:
    """Generate a fresh ticket number and persist it to the DB."""
    ticket_number = new_ticket()
    record_ticket(ticket_number, issue_summary)
    return ticket_number


def fetch_tickets() -> list[tuple]:
    conn = sqlite3.connect(TICKETS_DB)
    rows = conn.execute(
        "SELECT ticket_number, created_at, issue_summary, status "
        "FROM tickets ORDER BY created_at DESC").fetchall()
    conn.close()
    return rows


def set_ticket_status(ticket_number: str, status: str) -> None:
    conn = sqlite3.connect(TICKETS_DB)
    conn.execute("UPDATE tickets SET status = ? WHERE ticket_number = ?",
                 (status, ticket_number))
    conn.commit()
    conn.close()


# ────────────────────────────────────────────────
#  Web search (free, keyless — DuckDuckGo Instant Answer + Wikipedia)
# ────────────────────────────────────────────────
# Note: DuckDuckGo's HTML search endpoint now serves a bot-detection challenge to
# non-browser requests, so we use their official keyless Instant Answer API (great
# for factual/topic queries) and fall back to Wikipedia's search API for broader
# keyword coverage. Neither requires an API key or signup.
_TAG_RE = re.compile(r"<[^<]+?>")


def web_search(query: str, max_results: int = 4) -> list[dict]:
    """Keyless web search. Returns [] on any failure (network down, no results, etc.)."""
    results = []

    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading") or query,
                "snippet": data["AbstractText"],
                "url": data.get("AbstractURL") or "",
            })
        for topic in data.get("RelatedTopics", []):
            text, url = topic.get("Text"), topic.get("FirstURL")
            if text and url:
                results.append({"title": text.split(" - ")[0][:80], "snippet": text, "url": url})
            if len(results) >= max_results:
                break
    except requests.RequestException:
        pass

    # ServerFault (sysadmin Q&A) — far more relevant to IT-helpdesk-style
    # queries (error codes, "how do I fix X") than encyclopedia sources.
    if len(results) < max_results:
        try:
            resp = requests.get(
                "https://api.stackexchange.com/2.3/search/advanced",
                params={"q": query, "site": "serverfault", "pagesize": max_results,
                       "order": "desc", "sort": "relevance"},
                headers={"Accept-Encoding": "gzip"}, timeout=8)
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                title = html.unescape(item.get("title", ""))
                status = "answered" if item.get("is_answered") else "unanswered"
                snippet = f"ServerFault Q&A ({status}, score {item.get('score', 0)})"
                results.append({"title": title, "snippet": snippet, "url": item.get("link", "")})
                if len(results) >= max_results:
                    break
        except requests.RequestException:
            pass

    if len(results) < max_results:
        try:
            resp = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": query,
                       "format": "json", "srlimit": max_results},
                headers={"User-Agent": "ITSupportAssistant/1.0"}, timeout=8)
            resp.raise_for_status()
            for item in resp.json().get("query", {}).get("search", []):
                title = item.get("title", "")
                snippet = html.unescape(_TAG_RE.sub("", item.get("snippet", "")))
                url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
                results.append({"title": title, "snippet": snippet, "url": url})
                if len(results) >= max_results:
                    break
        except requests.RequestException:
            pass

    return results[:max_results]


def format_search_results(results: list[dict]) -> str:
    if not results:
        return "(no results found)"
    return "\n".join(
        f"{i}. {r['title']} — {r['snippet']} ({r['url']})"
        for i, r in enumerate(results, 1))


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)


def fetch_url_content(url: str, max_chars: int = 4000) -> dict:
    """
    Fetches a URL and returns its readable text (HTML stripped), so the
    assistant can read the full page behind a search result instead of only
    its snippet. Bounded to a max read size and text-like content types only.
    """
    if not url.lower().startswith(("http://", "https://")):
        return {"url": url, "error": "URL must start with http:// or https://"}
    try:
        resp = requests.get(
            url, headers={"User-Agent": "Mozilla/5.0 (ITSupportAssistant)"},
            timeout=10, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "text" not in content_type and "html" not in content_type:
            return {"url": url, "error": f"Unsupported content type: {content_type or 'unknown'}"}
        raw = resp.raw.read(500_000, decode_content=True)
        text = raw.decode(resp.encoding or "utf-8", errors="ignore")
    except requests.RequestException as exc:
        return {"url": url, "error": str(exc)}

    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return {"url": url, "content": text[:max_chars], "truncated": len(text) > max_chars}


# ────────────────────────────────────────────────
#  Ollama (local model server)
# ────────────────────────────────────────────────
def fetch_ollama_models() -> list[str]:
    """List locally-pulled Ollama models. Returns [] if Ollama isn't reachable."""
    try:
        resp = requests.get(OLLAMA_TAGS_URL, timeout=3)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except requests.RequestException:
        return []


# ────────────────────────────────────────────────
#  Internal IT knowledge base (local, keyword-retrieved)
# ────────────────────────────────────────────────
KB_FILE = os.path.join(_APP_DIR, "knowledge_base.json")

DEFAULT_KNOWLEDGE_BASE = [
    {
        "title": "Active Directory account lockout",
        "tags": ["account locked", "lockout", "active directory", "ad", "login"],
        "content": "Accounts lock after repeated bad password attempts (default policy: "
                   "5 attempts / 30 min lockout). Check Event Viewer Security log for "
                   "Event ID 4740 on the domain controller to find the source workstation "
                   "causing the lockout (often a cached credential in a phone, mapped "
                   "drive, or scheduled task using an old password). Unlock via Active "
                   "Directory Users and Computers > right-click account > Unlock Account, "
                   "or `Unlock-ADAccount -Identity <user>` in PowerShell.",
    },
    {
        "title": "Password reset (self-service vs admin)",
        "tags": ["password reset", "forgot password", "sspr"],
        "content": "If Self-Service Password Reset (SSPR) is enabled, direct the user to "
                   "the company SSO portal's 'Forgot Password' link and verify via MFA. "
                   "Otherwise reset via Active Directory Users and Computers or "
                   "`Set-ADAccountPassword`, and check 'User must change password at next "
                   "logon'. Remind the user to update saved credentials on mobile mail "
                   "apps and mapped drives after the reset to avoid an immediate lockout.",
    },
    {
        "title": "MFA / 2FA lockout recovery",
        "tags": ["mfa", "2fa", "multi-factor", "authenticator", "conditional access"],
        "content": "Common causes: lost/replaced phone, time drift on the authenticator "
                   "app (TOTP codes are time-based — check device clock sync), or the "
                   "user is out of registered backup methods. Fix: have an admin reset "
                   "MFA registration in Azure AD / Entra (Authentication methods > "
                   "Require re-register MFA), then have the user re-enroll. For a one-time "
                   "bypass, use a temporary access pass if your tenant supports it.",
    },
    {
        "title": "VPN connection failures",
        "tags": ["vpn", "remote access", "connection failed", "network"],
        "content": "Checklist: (1) confirm the user's internet works outside the VPN "
                   "client, (2) check VPN client version is current — outdated clients "
                   "often fail silently after a server-side update, (3) verify "
                   "credentials/cert haven't expired, (4) check for a conflicting local "
                   "subnet (home router using the same 192.168.x.x range as the office "
                   "VPN causes silent routing failures), (5) restart the VPN service or "
                   "reboot if the client hangs on 'Connecting'.",
    },
    {
        "title": "Wi-Fi / network connectivity issues",
        "tags": ["wifi", "wi-fi", "network", "no internet", "connectivity"],
        "content": "Standard order of operations: toggle Wi-Fi off/on, forget and "
                   "rejoin the network, run `ipconfig /release` then `/renew` (Windows) "
                   "or toggle Wi-Fi on macOS, flush DNS with `ipconfig /flushdns`. If only "
                   "one device is affected, suspect the NIC driver or adapter — check "
                   "Device Manager for a yellow warning icon and update/reinstall the "
                   "driver. If the whole office is affected, escalate to network team "
                   "immediately rather than troubleshooting per-device.",
    },
    {
        "title": "DNS resolution failures",
        "tags": ["dns", "dns not resolving", "name resolution", "cannot reach website"],
        "content": "Symptoms: can ping an IP but not a hostname. Fix order: "
                   "`ipconfig /flushdns`, confirm DNS server IPs in adapter settings "
                   "match company standard (not stale, not a dead secondary DNS), try a "
                   "public resolver (1.1.1.1 or 8.8.8.8) to isolate whether it's the "
                   "company DNS server itself that's down. If only internal hostnames "
                   "fail, the issue is almost always the internal DNS server/zone, not "
                   "the client.",
    },
    {
        "title": "Outlook / Exchange Online issues",
        "tags": ["outlook", "email", "exchange", "mail not syncing"],
        "content": "For sync issues: run Outlook in Safe Mode (`outlook.exe /safe`) to "
                   "rule out a bad add-in. For 'disconnected' status, check "
                   "outlook.office.com directly — if webmail works but the client "
                   "doesn't, the local OST file is often corrupt; rename/delete the .ost "
                   "(Outlook closed) and let it resync. For repeated credential prompts, "
                   "check for a stale entry in Windows Credential Manager under "
                   "'MicrosoftOffice16_Data'.",
    },
    {
        "title": "Slow computer performance triage",
        "tags": ["slow computer", "performance", "freezing", "lag"],
        "content": "Quick triage: Task Manager > Processes sorted by CPU/Memory/Disk to "
                   "find the offender. Common causes: a runaway browser tab process, "
                   "antivirus full-disk scan running mid-day, disk near-full (Windows "
                   "needs ~15% free space to perform normally), or too many startup apps "
                   "(check Task Manager > Startup tab, disable non-essential entries). "
                   "If disk activity is pegged at 100% with low CPU, suspect a failing "
                   "HDD/SSD — check S.M.A.R.T. status.",
    },
    {
        "title": "Printer not printing / not detected",
        "tags": ["printer", "printing", "print queue"],
        "content": "Check the print queue first — a single stuck job (especially a large "
                   "PDF) blocks everything behind it; clear with "
                   "`net stop spooler && del /Q %systemroot%\\System32\\spool\\PRINTERS\\* "
                   "&& net start spooler`. For network printers, confirm the printer's IP "
                   "hasn't changed (DHCP reassignment is a common silent cause). For "
                   "'printer offline' on a printer that's clearly powered on, remove and "
                   "re-add it rather than troubleshooting the existing queue entry.",
    },
    {
        "title": "Shared drive / file access permission issues",
        "tags": ["file access", "shared drive", "permissions", "denied access"],
        "content": "Confirm the user is in the correct AD security group for that share "
                   "(`whoami /groups` on their machine, or check Active Directory Users "
                   "and Computers > group Members tab). Group membership changes require "
                   "a logoff/logon (or `klist purge` + relogin) to refresh the Kerberos "
                   "ticket — a very common 'it still doesn't work' complaint right after "
                   "an admin adds someone to a group.",
    },
    {
        "title": "Software crashing / won't open",
        "tags": ["software crash", "application crash", "won't open", "not responding"],
        "content": "Check Event Viewer > Windows Logs > Application for the matching "
                   "crash event (note the faulting module — a corrupt DLL or outdated "
                   "GPU driver are common culprits). Try running as a different user "
                   "profile to rule out profile corruption. For Office apps specifically, "
                   "an Online Repair (Control Panel > Programs > [app] > Change > Online "
                   "Repair) fixes the majority of crash-on-launch cases.",
    },
    {
        "title": "Windows Update stuck or failing",
        "tags": ["windows update", "update failed", "update stuck"],
        "content": "Run the built-in Windows Update Troubleshooter first (Settings > "
                   "Troubleshoot > Other troubleshooters). If that doesn't resolve it, "
                   "stop the Windows Update service, clear "
                   "`C:\\Windows\\SoftwareDistribution\\Download`, and restart the "
                   "service — this clears a corrupted partial download, the most common "
                   "cause of updates that fail at the same percentage repeatedly.",
    },
    {
        "title": "New software / application install requests",
        "tags": ["new software", "install request", "admin rights"],
        "content": "Standard policy: end users don't have local admin rights, so "
                   "software installs go through the approved software catalog "
                   "(Company Portal / Software Center) where available. For anything not "
                   "in the catalog, the request needs manager approval and a license "
                   "check before IT pushes the install — don't grant temporary local "
                   "admin as a shortcut, it bypasses the approval and audit trail.",
    },
    {
        "title": "BitLocker recovery key requests",
        "tags": ["bitlocker", "recovery key", "encrypted drive"],
        "content": "BitLocker prompts for a recovery key after hardware changes (new "
                   "motherboard, BIOS update, boot order change) or too many failed boot "
                   "attempts. Recovery keys for domain-joined machines are backed up to "
                   "Active Directory or Azure AD/Entra automatically — look up the "
                   "computer object's 'BitLocker Recovery' tab, or in Entra under the "
                   "device's BitLocker keys blade. Never have a user read a recovery key "
                   "aloud over an unverified call — confirm identity first.",
    },
    {
        "title": "Blue Screen of Death (BSOD) triage",
        "tags": ["bsod", "blue screen", "stop code", "crash", "kernel panic"],
        "content": "Note the exact STOP CODE (e.g. IRQL_NOT_LESS_OR_EQUAL, "
                   "DRIVER_IRQL_NOT_LESS_OR_EQUAL, PAGE_FAULT_IN_NONPAGED_AREA) — it almost "
                   "always points to a specific faulting driver, named in the crash dump. "
                   "Check `C:\\Windows\\Minidump` or use WinDbg/whocrashed to identify the "
                   "module. Common causes: a recently updated GPU/storage driver (roll it "
                   "back via Device Manager), failing RAM (run `mdsched.exe` / Windows "
                   "Memory Diagnostic), or a failing disk (check S.M.A.R.T. status). If "
                   "BSODs started right after a Windows Update, try System Restore to a "
                   "point before the update.",
    },
    {
        "title": "Phishing / suspicious email handling",
        "tags": ["phishing", "suspicious email", "spam", "scam", "spoofing"],
        "content": "Do not click links or open attachments. Check the actual sender address "
                   "(not just the display name) and hover over links to see the real "
                   "destination URL before anything else. Have the user use the 'Report "
                   "Phishing' button if your mail platform has one (Outlook/Gmail both do) "
                   "— this also trains the spam filter. Escalate to security/IT immediately "
                   "if the user already clicked a link or entered credentials: force a "
                   "password reset and check sign-in activity for unfamiliar locations/IPs. "
                   "Never confirm or deny account details over an inbound call/email "
                   "claiming to be from IT — verify identity via a known internal channel.",
    },
    {
        "title": "Mobile device email / MFA app setup",
        "tags": ["mobile", "phone", "email setup", "outlook mobile", "authenticator app"],
        "content": "Most email setup failures on mobile are due to Conditional Access "
                   "policies requiring the official Outlook or company portal app instead "
                   "of the phone's built-in mail app — check if Modern Authentication / "
                   "App Protection Policies are enforced before troubleshooting further. "
                   "For Authenticator app setup, the most common failure is a stale QR/setup "
                   "code (codes expire after a few minutes — regenerate from the account "
                   "security page rather than reusing an old screenshot). If notifications "
                   "aren't arriving, check the phone's battery-optimization settings aren't "
                   "killing the app in the background.",
    },
    {
        "title": "Browser slow / high memory usage",
        "tags": ["browser", "chrome", "edge", "slow browser", "extensions"],
        "content": "Open the browser's built-in task manager (Shift+Esc in Chrome/Edge) to "
                   "find which tab or extension is consuming CPU/memory. A single runaway "
                   "extension is a very common cause — try a clean profile or disable "
                   "extensions one-by-one. Clearing cache/cookies fixes a surprising number "
                   "of 'site is broken/slow' complaints (Settings > Privacy > Clear browsing "
                   "data). If the whole browser is sluggish system-wide, check how many tabs "
                   "are open — each tab is a separate process; 50+ tabs on a low-RAM machine "
                   "will make everything feel slow.",
    },
    {
        "title": "Cloud storage sync issues (OneDrive / Google Drive)",
        "tags": ["onedrive", "google drive", "sync", "cloud storage", "file not syncing"],
        "content": "Check the sync client's tray icon first — a red/yellow icon means it's "
                   "paused or has an error, hover/click for the specific reason. Common "
                   "causes: file path too long (OneDrive has a ~400 character limit), a "
                   "file open in another app blocking sync, or the account needing "
                   "re-authentication after a password change. 'Files On-Demand' (OneDrive) "
                   "can make files look present but not actually downloaded — right-click > "
                   "'Always keep on this device' if a user needs guaranteed offline access. "
                   "For a fully stuck sync, unlink and relink the account (this does not "
                   "delete cloud files).",
    },
    {
        "title": "Teams / Zoom audio and video issues",
        "tags": ["teams", "zoom", "no audio", "camera not working", "meeting issues"],
        "content": "Check the OS-level privacy settings first — Windows/macOS both have a "
                   "per-app camera/microphone permission toggle that silently blocks access "
                   "without an obvious error in the app itself. If audio works but is "
                   "choppy, it's almost always network (Wi-Fi signal or VPN routing all "
                   "traffic through a slow tunnel) rather than the app. For 'camera in use "
                   "by another application' errors, fully quit other apps that may hold the "
                   "camera (browser tabs with camera permission count too). A fresh sign-out "
                   "and back in resolves a large share of 'stuck loading' meeting issues.",
    },
    {
        "title": "Remote Desktop (RDP) connection failures",
        "tags": ["rdp", "remote desktop", "remote connection", "cannot connect"],
        "content": "Confirm the target machine allows Remote Desktop connections (System "
                   "Properties > Remote) and that the user is in the 'Remote Desktop Users' "
                   "group if not an admin. 'An internal error has occurred' is frequently a "
                   "stale credential — clear saved credentials via Credential Manager and "
                   "reconnect. If RDP works on the LAN but not remotely, check whether the "
                   "VPN is actually routing traffic to that subnet, and whether the target "
                   "machine is awake (RDP can't wake a sleeping PC unless Wake-on-LAN is "
                   "configured).",
    },
    {
        "title": "Authentication failures from clock drift",
        "tags": ["kerberos", "clock skew", "time sync", "authentication failed"],
        "content": "Kerberos authentication (used by Active Directory) fails if a machine's "
                   "clock drifts more than 5 minutes from the domain controller — this shows "
                   "up as mysterious login failures, mapped-drive access denials, or 'clock "
                   "skew too great' in event logs, despite a correct password. Fix: confirm "
                   "the workstation is syncing time from the domain (`w32tm /query /status`) "
                   "and force a resync (`w32tm /resync`) if needed. A VM that's been "
                   "suspended/paused for a long time is a classic culprit for this.",
    },
    {
        "title": "USB device not recognized",
        "tags": ["usb", "device not recognized", "peripheral", "driver"],
        "content": "Try the device in a different USB port first (USB controllers can fail "
                   "independently) and a different cable if applicable. Check Device Manager "
                   "for a yellow warning icon under 'Other devices' or 'Universal Serial Bus "
                   "controllers' — a missing driver shows up there even if Windows says "
                   "'not recognized'. USB 3.0 ports (usually blue) occasionally have "
                   "compatibility issues with older USB 2.0 peripherals; try a USB 2.0 port "
                   "if available. For 'USB device has malfunctioned' repeatedly on the same "
                   "port, suspect a power-delivery issue — try a powered USB hub.",
    },
    {
        "title": "Group Policy not applying",
        "tags": ["group policy", "gpo", "policy not applying"],
        "content": "Run `gpresult /r` on the affected machine to see which GPOs are actually "
                   "applied vs expected — this immediately shows if the GPO is being blocked "
                   "by security filtering, WMI filtering, or simply isn't linked to the "
                   "right OU. Force a refresh with `gpupdate /force` (a logoff/reboot is "
                   "needed for some computer-level policies to take effect, not just user "
                   "ones). If a GPO was JUST changed and isn't showing yet, remember "
                   "replication across domain controllers and the default 90-120 minute "
                   "background refresh interval — don't assume it's broken if it's been "
                   "less than that.",
    },
    {
        "title": "Software activation / licensing errors",
        "tags": ["activation", "license", "not genuine", "license expired"],
        "content": "For Windows 'not activated' errors, check Settings > Activation for the "
                   "specific error code — 0xC004F074 typically means it can't reach the KMS "
                   "activation server (network/firewall issue), while 0xC004C003 usually "
                   "means the product key has been blocked/exceeded its activation limit. "
                   "For Office/Microsoft 365 'unlicensed product' banners, the most common "
                   "cause is the user's license assignment lapsed or changed in the admin "
                   "center — verify in Microsoft 365 admin center > Users > Licenses before "
                   "troubleshooting the client further.",
    },
    {
        "title": "Multi-monitor / display setup issues",
        "tags": ["dual monitor", "multiple monitors", "display not detected", "extend display"],
        "content": "If a second monitor isn't detected, try a different cable/port first "
                   "(especially when mixing HDMI/DisplayPort via adapters — passive adapters "
                   "between incompatible signal types don't work). Windows Settings > "
                   "Display > Detect forces a rescan if a monitor was connected after boot. "
                   "For docking-station setups, the dock's firmware/drivers (not just the "
                   "GPU driver) often need updating — check the dock vendor's site. "
                   "Resolution/scaling mismatches between monitors causing blurry text are "
                   "fixed via per-monitor scaling in Display Settings, not a global setting.",
    },
]


def load_knowledge_base() -> list[dict]:
    """Load the local IT knowledge base, seeding it with defaults on first run."""
    if not os.path.exists(KB_FILE):
        save_knowledge_base(DEFAULT_KNOWLEDGE_BASE)
        return list(DEFAULT_KNOWLEDGE_BASE)
    try:
        with open(KB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else list(DEFAULT_KNOWLEDGE_BASE)
    except (json.JSONDecodeError, OSError):
        return list(DEFAULT_KNOWLEDGE_BASE)


def save_knowledge_base(entries: list[dict]) -> None:
    try:
        with open(KB_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def _normalize_word(word: str) -> str:
    """Very light stemming so plurals/verb forms still match (e.g. 'monitors'/'monitor',
    'syncing'/'sync', 'crashes'/'crash') without pulling in a real NLP dependency."""
    w = word.lower()
    for suffix in ("ing", "es", "ed", "s"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def _normalized_words(text: str) -> set:
    return {_normalize_word(w) for w in _WORD_RE.findall(text)}


def search_knowledge_base(query: str, kb: list[dict], max_results: int = 4) -> list[dict]:
    """
    Keyword-overlap retrieval weighted by field — a match in the title or tags
    is a much stronger relevance signal than the same word appearing somewhere
    in the body text, so it's weighted accordingly. No embeddings/vector DB
    needed for a knowledge base this size.
    """
    query_words = _normalized_words(query)
    if not query_words:
        return []
    scored = []
    for entry in kb:
        title_words = _normalized_words(entry.get("title", ""))
        tag_words = _normalized_words(" ".join(entry.get("tags", [])))
        body_words = _normalized_words(entry.get("content", ""))
        score = (3 * len(query_words & title_words)
                + 2 * len(query_words & tag_words)
                + 1 * len(query_words & body_words))
        if score > 0:
            scored.append((score, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in scored[:max_results]]


def format_kb_results(entries: list[dict]) -> str:
    if not entries:
        return "(no matching internal articles)"
    return "\n".join(f"{i}. {e['title']}: {e['content']}" for i, e in enumerate(entries, 1))


# ────────────────────────────────────────────────
#  Real local diagnostics — allowlisted, read-only Windows commands only.
#  No arbitrary command execution: `action` must be a key in this dict, and
#  `target` (when required) is validated against a strict pattern before
#  being placed in a non-shell argv list, so there's no injection surface.
# ────────────────────────────────────────────────
_VALID_TARGET_RE = re.compile(r"^[a-zA-Z0-9_.\-:]{1,253}$")

DIAGNOSTIC_ACTIONS = {
    "network_config": {
        "description": "Network adapter configuration — IP, DNS servers, gateway",
        "command": ["ipconfig", "/all"],
        "needs_target": False,
    },
    "dns_lookup": {
        "description": "Resolve a hostname via DNS",
        "command": ["nslookup", "{target}"],
        "needs_target": True,
    },
    "ping": {
        "description": "Ping a host 4 times to test reachability/latency",
        "command": ["ping", "-n", "4", "{target}"],
        "needs_target": True,
    },
    "traceroute": {
        "description": "Trace the network path to a host (max 15 hops)",
        "command": ["tracert", "-h", "15", "{target}"],
        "needs_target": True,
    },
    "system_info": {
        "description": "OS version, hardware, installed hotfixes",
        "command": ["systeminfo"],
        "needs_target": False,
    },
    "service_status": {
        "description": "Check whether a named Windows service is running",
        "command": ["sc", "query", "{target}"],
        "needs_target": True,
    },
    "group_policy_result": {
        "description": "Applied Group Policy objects for this computer/user",
        "command": ["gpresult", "/r"],
        "needs_target": False,
    },
    "disk_space": {
        "description": "Free/total disk space per drive",
        "command": ["wmic", "logicaldisk", "get", "size,freespace,caption"],
        "needs_target": False,
    },
    "active_connections": {
        "description": "Active network connections and listening ports",
        "command": ["netstat", "-an"],
        "needs_target": False,
    },
}


def run_diagnostic(action: str, target: str = "") -> dict:
    """Runs an allowlisted, read-only Windows diagnostic and returns its real output."""
    if platform.system() != "Windows":
        return {"error": "Local diagnostics are currently only implemented for Windows."}

    spec = DIAGNOSTIC_ACTIONS.get(action)
    if not spec:
        return {"error": f"Unknown diagnostic action '{action}'. "
                         f"Valid actions: {', '.join(DIAGNOSTIC_ACTIONS)}"}

    if spec["needs_target"]:
        if not target or not _VALID_TARGET_RE.match(target):
            return {"error": "A valid target (hostname, IP, or service name) is required."}
        command = [part.format(target=target) for part in spec["command"]]
    else:
        command = list(spec["command"])

    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"error": f"'{action}' timed out after 15 seconds."}
    except OSError as exc:
        return {"error": f"Could not run '{action}': {exc}"}

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {
        "action": action,
        "command": " ".join(command),
        "output": output[:4000],
        "truncated": len(output) > 4000,
    }


# ────────────────────────────────────────────────
#  Tool use (Claude function calling) — simulated AD directory + real ticket lookup
# ────────────────────────────────────────────────
MAX_TOOL_ITERATIONS = 4

TOOL_DEFS = [
    {
        "name": "check_account_status",
        "description": "Look up a user's Active Directory account status: locked/unlocked, "
                       "last logon time, group memberships, MFA enrollment, department. "
                       "Uses a simulated demo directory, not a real company AD.",
        "input_schema": {
            "type": "object",
            "properties": {"username": {"type": "string",
                                        "description": "AD username/login, e.g. jdoe"}},
            "required": ["username"],
        },
    },
    {
        "name": "unlock_account",
        "description": "Unlock a locked Active Directory account. Simulated demo action — "
                       "does not affect any real directory.",
        "input_schema": {
            "type": "object",
            "properties": {"username": {"type": "string"}},
            "required": ["username"],
        },
    },
    {
        "name": "trigger_password_reset",
        "description": "Trigger a password reset for a user and generate a temporary "
                       "password. Simulated demo action.",
        "input_schema": {
            "type": "object",
            "properties": {"username": {"type": "string"}},
            "required": ["username"],
        },
    },
    {
        "name": "check_ticket_status",
        "description": "Look up a support ticket's real status, creation time, and issue "
                       "summary from the local ticket database.",
        "input_schema": {
            "type": "object",
            "properties": {"ticket_number": {"type": "string",
                                             "description": "Format TKT-XXXXX"}},
            "required": ["ticket_number"],
        },
    },
    {
        "name": "search_the_web",
        "description": "Search the live web for current information not in your training "
                       "data or the internal knowledge base — recent error codes, software "
                       "version-specific bugs, vendor advisories, or sysadmin Q&A. Pulls from "
                       "DuckDuckGo, Wikipedia, and ServerFault. Returns titles, snippets, and "
                       "URLs — use fetch_webpage on a promising URL to read the full page.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
    {
        "name": "fetch_webpage",
        "description": "Fetch and read the full text content of a specific URL — e.g. to "
                       "read the complete page behind a search_the_web result instead of "
                       "just its snippet. Works on most articles/docs/forum pages.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string",
                                   "description": "Full URL including https://"}},
            "required": ["url"],
        },
    },
    {
        "name": "search_internal_kb",
        "description": "Search the internal IT knowledge base directly with your own query "
                       "(it's also auto-searched using the user's raw message, but you can "
                       "call this with a more targeted or reformulated query — e.g. after a "
                       "follow-up clarifies the actual issue — to find company procedures "
                       "the first automatic search missed).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]

# A separate, off-by-default tool def (not part of TOOL_DEFS) — it actually
# executes commands on the user's real machine, so it gets its own explicit
# opt-in in Settings rather than being bundled with the always-on tools above.
DIAGNOSTIC_TOOL_DEF = {
    "name": "run_diagnostic",
    "description": "Run a real, read-only diagnostic command on the user's actual computer and "
                   "see its real output — network config, DNS lookup, ping, traceroute, system "
                   "info, service status, Group Policy result, disk space, or active "
                   "connections. Use this instead of guessing when you can directly verify "
                   "something (don't say 'check your IP configuration' — run network_config "
                   "and read the real result yourself).",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(DIAGNOSTIC_ACTIONS.keys()),
                      "description": "Which diagnostic to run"},
            "target": {"type": "string",
                      "description": "Hostname/IP/service name — required for dns_lookup, "
                                     "ping, traceroute, service_status"},
        },
        "required": ["action"],
    },
}


def to_ollama_tool_format(tool_def: dict) -> dict:
    """Anthropic's flat {name, description, input_schema} -> Ollama/OpenAI's
    nested {"type": "function", "function": {name, description, parameters}}."""
    return {
        "type": "function",
        "function": {
            "name": tool_def["name"],
            "description": tool_def["description"],
            "parameters": tool_def["input_schema"],
        },
    }


_MOCK_AD_DIRECTORY: dict[str, dict] = {}


def _get_mock_account(username: str) -> dict:
    """Deterministic-per-username fake AD record, generated lazily on first lookup."""
    key = username.lower().strip()
    if key not in _MOCK_AD_DIRECTORY:
        rnd = random.Random(sum(ord(c) for c in key) or 1)
        all_groups = ["Domain Users", "VPN-Users", "Finance-ReadOnly", "IT-Staff",
                      "Sales-Team", "Remote-Workers", "SharePoint-Editors"]
        _MOCK_AD_DIRECTORY[key] = {
            "username": key,
            "locked": rnd.random() < 0.35,
            "last_logon": (datetime.now() - timedelta(
                days=rnd.randint(0, 5), hours=rnd.randint(0, 23))).strftime("%Y-%m-%d %H:%M"),
            "groups": rnd.sample(all_groups, k=rnd.randint(2, 4)),
            "mfa_enrolled": rnd.random() < 0.8,
            "department": rnd.choice(["Finance", "Sales", "Engineering", "HR", "Support"]),
        }
    return _MOCK_AD_DIRECTORY[key]


def tool_check_account_status(args: dict) -> dict:
    acct = _get_mock_account(args.get("username", ""))
    return {**acct, "note": "Simulated directory lookup — demo data, not a real AD."}


def tool_unlock_account(args: dict) -> dict:
    username = args.get("username", "")
    acct = _get_mock_account(username)
    was_locked = acct["locked"]
    acct["locked"] = False
    return {"username": username, "was_locked": was_locked, "now_locked": False,
            "note": "Simulated unlock — demo action only."}


def tool_trigger_password_reset(args: dict) -> dict:
    username = args.get("username", "")
    _get_mock_account(username)
    temp_password = "Temp" + "".join(random.choices(string.digits, k=4)) + "!"
    return {"username": username, "reset_triggered": True,
            "temporary_password": temp_password, "must_change_at_next_logon": True,
            "note": "Simulated reset — demo action only."}


def tool_check_ticket_status(args: dict) -> dict:
    ticket_number = args.get("ticket_number", "")
    conn = sqlite3.connect(TICKETS_DB)
    row = conn.execute(
        "SELECT ticket_number, created_at, issue_summary, status FROM tickets "
        "WHERE ticket_number = ?", (ticket_number,)).fetchone()
    conn.close()
    if not row:
        return {"found": False, "ticket_number": ticket_number}
    return {"found": True, "ticket_number": row[0], "created_at": row[1],
            "issue_summary": row[2], "status": row[3]}


def tool_search_the_web(args: dict) -> dict:
    query = args.get("query", "")
    results = web_search(query)
    return {"query": query, "results": results,
            "note": "no results found" if not results else None}


def tool_fetch_webpage(args: dict) -> dict:
    return fetch_url_content(args.get("url", ""))


def tool_run_diagnostic(args: dict) -> dict:
    return run_diagnostic(args.get("action", ""), args.get("target", ""))


TOOL_FUNCTIONS = {
    "check_account_status":   tool_check_account_status,
    "unlock_account":         tool_unlock_account,
    "trigger_password_reset": tool_trigger_password_reset,
    "check_ticket_status":    tool_check_ticket_status,
    "search_the_web":         tool_search_the_web,
    "fetch_webpage":          tool_fetch_webpage,
    "run_diagnostic":         tool_run_diagnostic,
}


SYSTEM_PROMPT = """You are an expert AI-powered IT Support Assistant for a professional help desk.

Your responsibilities:
1. Troubleshoot hardware, software, and network connectivity issues step-by-step
2. Assist with account management, Active Directory permissions, and password resets
3. Support Windows/macOS operating systems, Microsoft 365, Exchange Online, and SharePoint
4. Guide non-technical users with clear, numbered instructions
5. Generate a ticket reference number (format TKT-XXXXX) whenever an issue needs escalation
6. Maintain full awareness of the conversation — refer back to prior context when answering follow-up questions

Communication rules:
- Be professional, patient, and concise
- Always ask a clarifying question if the issue is unclear before providing steps
- When listing steps, use numbered lists
- End each response by confirming whether the issue is resolved or asking what to try next
- If a problem cannot be solved remotely, provide an escalation path with a ticket number

Troubleshooting methodology — think like an experienced sysadmin, not a script:
- Diagnose before prescribing. If the report is vague ("it's not working"), ask exactly one sharp
  clarifying question that narrows the cause the most (exact error text/code, when it started, what
  changed recently, does it affect one device/user or many) rather than dumping a generic checklist.
- Order steps by (a) most likely cause given the symptoms and (b) cheapest/least disruptive to try
  first — don't jump to "reinstall" or "reimage" before the 30-second checks (reboot, check cable, check
  password expiry, check service status).
- Isolate variables. If something "suddenly stopped working", the most useful question is usually
  "what changed?" — a recent update, a password change, a new device, a policy push. Symptoms that hit
  one user point at their account/device; symptoms hitting many users at once point at infrastructure.
- State your reasoning briefly when it's not obvious ("since this only started after the Windows
  Update, let's check..." ) so the user understands why you're trying something, not just what to click.
- Don't repeat a fix you already confirmed didn't work earlier in this conversation — adjust the
  hypothesis instead of re-suggesting the same thing.

Your technical expertise covers: Active Directory, Windows 10/11, macOS, Microsoft 365, Exchange Online,
SharePoint, Teams, Remote Desktop Tools, Service Desk Plus, hardware diagnostics, basic networking,
VPN, MFA / Conditional Access, and general IT support procedures.

Some messages will include extra reference context appended after the user's question, labeled either
"[Internal knowledge base ...]" (company-specific procedures — prefer these over general knowledge when
they apply) or "[Live web search results ...]" (current external information). Use this context only when
it's actually relevant to the question, weave it naturally into your answer, and don't mention the
mechanism (e.g. don't say "according to the knowledge base provided") — just answer like you know it.

You also have tools to actually check and act on things instead of only describing steps:
check_account_status, unlock_account, trigger_password_reset (a simulated demo Active Directory —
not a real company directory) and check_ticket_status (the real local ticket database). When a user's
request calls for looking something up or taking an account action — "is my account locked", "reset my
password", "what's the status of ticket TKT-12345" — call the relevant tool and report the actual result,
rather than just explaining the manual steps a human admin would take.

You also have search_internal_kb — the knowledge base is auto-searched using the user's raw message
already, but call this yourself with a better-targeted query once you understand the real issue (e.g.
after a clarifying question), especially if the auto-search's results didn't quite fit.

You also have search_the_web and fetch_webpage. Use search_the_web whenever a question needs current
information you're not confident about — a specific error code, a recent CVE, a vendor's current
documented fix, a version-specific bug — instead of guessing or giving generic advice. If a search result
looks like the right page but the snippet isn't enough, call fetch_webpage on its URL to read the full
page before answering. Always cite the URL when you used a web result. Don't bother searching for things
you already know confidently (general troubleshooting steps, well-established procedures).

If run_diagnostic is available (the user opts into this explicitly in Settings — assume it may not be),
use it to actually verify things on their real machine instead of describing what they should check
themselves: network_config, dns_lookup, ping, traceroute, system_info, service_status,
group_policy_result, disk_space, active_connections. This is the single highest-value thing you can do
when it's available — prefer "let me check that" + a real diagnostic over generic advice whenever one of
these actions could directly confirm or rule out a cause. It only runs read-only commands; it cannot
change anything on the machine.

A user may also attach a screenshot to their message (e.g. of an error dialog, BSOD, or a confusing UI
state). Read it directly and address what's actually shown — quote the exact error text/code visible
in the image rather than asking the user to re-type it."""

DARK_COLORS = {
    "bg_dark":      "#0b0b12",
    "bg_panel":     "#11111a",
    "bg_input":     "#181822",
    "accent":       "#2a2a38",
    "accent_lit":   "#e2725b",
    "user_bubble":  "#1c2230",
    "ai_bubble":    "#15151f",
    "text_primary": "#eae8e6",
    "text_dim":     "#8b8b9a",
    "text_ghost":   "#52525f",
    "border":       "#22222e",
    "send_btn":     "#e2725b",
    "send_hover":   "#c75d48",
    "status_ok":    "#4caf50",
    "header_bg":    "#0b0b12",
}

LIGHT_COLORS = {
    "bg_dark":      "#f7f4f2",
    "bg_panel":     "#ffffff",
    "bg_input":     "#f0ece9",
    "accent":       "#ece6e2",
    "accent_lit":   "#d1573f",
    "user_bubble":  "#fbe3dd",
    "ai_bubble":    "#f2efec",
    "text_primary": "#231f1d",
    "text_dim":     "#6b6560",
    "text_ghost":   "#a39d97",
    "border":       "#e6e1dc",
    "send_btn":     "#d1573f",
    "send_hover":   "#b8472f",
    "status_ok":    "#2e7d32",
    "header_bg":    "#ffffff",
}

THEMES = {"dark": DARK_COLORS, "light": LIGHT_COLORS}

# Backwards-compatible default (used before the app instance picks a theme)
COLORS = DARK_COLORS

QUICK_ACTIONS = [
    ("🔑  Password Reset",     "I need to reset my password and can't log in."),
    ("🔒  Account Locked",     "My account is locked and I cannot log in."),
    ("🌐  Network / VPN",      "I can't connect to the network or VPN."),
    ("📧  Email / Outlook",    "I'm having a problem with my email or Outlook."),
    ("💻  Slow Computer",      "My computer is running very slowly."),
    ("🖨️  Printer Not Working","My printer isn't printing or isn't recognised."),
    ("📁  File / Drive Access","I can't access a file or a shared drive."),
    ("📱  MFA / 2FA Issue",    "I'm locked out because of a multi-factor auth issue."),
    ("🖥️  Software Crash",     "An application keeps crashing or won't open."),
    ("📦  New Software Request","I need a new application installed on my computer."),
]


# ────────────────────────────────────────────────
#  Helper
# ────────────────────────────────────────────────
def new_ticket() -> str:
    return "TKT-" + "".join(random.choices(string.digits, k=5))


# ────────────────────────────────────────────────
#  Tooltip — lightweight hover hint for icon-only buttons
# ────────────────────────────────────────────────
class Tooltip:
    """Shows a small hint label near a widget after a brief hover delay."""

    def __init__(self, widget, text: str, delay: int = 450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._tip_window = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        if self._tip_window or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        try:
            tw.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify=tk.LEFT,
                bg="#1a1a2e", fg="#eaeaea", relief=tk.SOLID, borderwidth=1,
                font=("Segoe UI", 8), padx=7, pady=4).pack()

    def _hide(self, _event=None):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip_window is not None:
            self._tip_window.destroy()
            self._tip_window = None


def add_tooltip(widget, text: str) -> Tooltip:
    return Tooltip(widget, text)


# ────────────────────────────────────────────────
#  Image attachments (screenshot / vision support — Claude only)
# ────────────────────────────────────────────────
MAX_IMAGE_DIMENSION = 1568   # Anthropic's recommended max before it downscales server-side anyway
IMAGE_FILETYPES = [("Images", "*.png *.jpg *.jpeg *.gif *.webp *.bmp")]


def encode_image_for_api(path: str) -> dict:
    """
    Opens an image file, downscales it if very large, and returns a dict ready
    to drop into an Anthropic image content block: {"media_type", "data"}.
    Raises if PIL isn't available or the file isn't a readable image.
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow isn't installed — image attachments aren't available.")
    img = PILImage.open(path)
    img.load()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > MAX_IMAGE_DIMENSION:
        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {"media_type": "image/png", "data": base64.b64encode(buf.getvalue()).decode("ascii")}


def make_thumbnail_photo(b64_data: str, max_size: int = 220):
    """Decodes a base64 PNG and returns a Tk-displayable, size-capped PhotoImage."""
    if not PIL_AVAILABLE:
        return None
    raw = base64.b64decode(b64_data)
    img = PILImage.open(io.BytesIO(raw))
    img.thumbnail((max_size, max_size))
    return ImageTk.PhotoImage(img)


# ────────────────────────────────────────────────
#  Rounded-corner drawing helpers (Tkinter has no native border-radius, so
#  rounded panels/buttons/bubbles are Canvas-drawn, hosting real widgets on
#  top via create_window).
#
#  Note: the classic "smoothed polygon" trick for rounded rects renders
#  inconsistently on some Tk builds — it only rounded the top two corners
#  here, leaving the bottom two square. Built from primitive arcs + rects
#  instead, which can't have that ambiguity.
# ────────────────────────────────────────────────
_RRECT_COUNTER = itertools.count()


def draw_rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius=14,
                      fill="", outline="", outline_width=1, tag=None) -> str:
    """
    Draws (or redraws, if `tag` already has items) a rounded rectangle.
    Returns the tag string — pass it back in via `tag=` to resize/restyle,
    or use it directly with itemconfig/tag_bind/tag_lower like an item id.
    """
    if tag is None:
        tag = f"rrect{next(_RRECT_COUNTER)}"
    # Arcs use the "outline" option for their stroke color; plain lines only
    # have "fill" (no "outline" option at all — itemconfig errors if you try).
    # Keep them on separate sub-tags so callers can restyle the border without
    # hitting a TclError or touching the fill items' seam-hiding outline=fill.
    border_arc_tag = f"{tag}:border_arc"
    border_line_tag = f"{tag}:border_line"
    canvas.delete(tag)
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))

    if radius <= 1:
        canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline,
                                width=outline_width, tags=(tag, border_arc_tag))
        return tag

    d = 2 * radius
    corners = [(x1, y1, 90), (x2 - d, y1, 0), (x2 - d, y2 - d, 270), (x1, y2 - d, 180)]

    if fill:
        for ax1, ay1, start in corners:
            canvas.create_arc(ax1, ay1, ax1 + d, ay1 + d, start=start, extent=90,
                              fill=fill, outline=fill, style=tk.PIESLICE, tags=(tag,))
        canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2,
                                fill=fill, outline=fill, tags=(tag,))
        canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius,
                                fill=fill, outline=fill, tags=(tag,))

    if outline:
        for ax1, ay1, start in corners:
            canvas.create_arc(ax1, ay1, ax1 + d, ay1 + d, start=start, extent=90,
                              style=tk.ARC, outline=outline, width=outline_width,
                              tags=(tag, border_arc_tag))
        for lx1, ly1, lx2, ly2 in [
            (x1 + radius, y1, x2 - radius, y1), (x1 + radius, y2, x2 - radius, y2),
            (x1, y1 + radius, x1, y2 - radius), (x2, y1 + radius, x2, y2 - radius),
        ]:
            canvas.create_line(lx1, ly1, lx2, ly2, fill=outline, width=outline_width,
                              tags=(tag, border_line_tag))

    return tag


def set_rounded_rect_outline(canvas: tk.Canvas, tag: str, color: str) -> None:
    """Restyle a rounded rect's border (set color="" to hide it)."""
    canvas.itemconfig(f"{tag}:border_arc", outline=color)
    canvas.itemconfig(f"{tag}:border_line", fill=color)


def _measure_rendered_text(parent, text: str, font) -> tuple[int, int]:
    """
    Real rendered (width, height) of `text` in `font`. Emoji glyphs render via a
    fallback color-emoji font whose actual size can exceed what tkfont's
    plain-text metrics predict — using a throwaway probe canvas to get the true
    bbox avoids clipping icon glyphs against a too-small button/badge.
    """
    probe = tk.Canvas(parent, highlightthickness=0)
    probe_id = probe.create_text(0, 0, text=text, font=font, anchor="nw")
    probe.update_idletasks()
    bbox = probe.bbox(probe_id)
    probe.destroy()
    if bbox:
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    measurer = tkfont.Font(font=font)
    return measurer.measure(text), measurer.metrics("linespace")


def make_pill_button(parent, text: str, command, bg: str, fg: str, colors: dict,
                     hover_bg: str | None = None, font=("Segoe UI", 9),
                     radius: int = 12, padx: int = 14, pady: int = 8,
                     min_width: int = 0, min_height: int = 0) -> tk.Canvas:
    """A rounded-rect button drawn on a Canvas (real Tk buttons have square corners)."""
    hover_bg = hover_bg or bg
    text_w, text_h = _measure_rendered_text(parent, text, font)
    w = max(text_w + 2 * padx, min_width)
    h = max(text_h + 2 * pady, min_height)

    cv = tk.Canvas(parent, width=w, height=h, bg=parent["bg"],
                   highlightthickness=0, cursor="hand2")
    rect = draw_rounded_rect(cv, 1, 1, w - 1, h - 1, radius=radius, fill=bg, outline="")
    label = cv.create_text(w / 2, h / 2, text=text, fill=fg, font=font)

    cv._enabled = True

    def handle_click(_event=None):
        if cv._enabled:
            command()

    def _recolor(color: str):
        # The fill arcs/rects use outline=fill to hide their internal seams
        # (no real border was requested), so fill and outline must always be
        # changed together — updating fill alone leaves stale-colored seams.
        cv.itemconfig(rect, fill=color, outline=color)

    def on_enter(_event=None):
        if cv._enabled:
            _recolor(hover_bg)

    def on_leave(_event=None):
        if cv._enabled:
            _recolor(bg)

    cv.tag_bind(rect, "<Button-1>", handle_click)
    cv.tag_bind(label, "<Button-1>", handle_click)
    cv.tag_bind(rect, "<Enter>", on_enter)
    cv.tag_bind(label, "<Enter>", on_enter)
    cv.tag_bind(rect, "<Leave>", on_leave)
    cv.tag_bind(label, "<Leave>", on_leave)

    def set_enabled(enabled: bool):
        cv._enabled = enabled
        _recolor(bg if enabled else colors["text_ghost"])
        cv.configure(cursor="hand2" if enabled else "arrow")

    cv.set_enabled = set_enabled
    return cv


def make_pill_label(parent, text: str, bg: str, fg: str, font=("Segoe UI", 9, "bold"),
                    radius: int = 12, padx: int = 12, pady: int = 6) -> tk.Canvas:
    """A static rounded pill — used for badges that aren't clickable."""
    text_w, text_h = _measure_rendered_text(parent, text, font)
    w = text_w + 2 * padx
    h = text_h + 2 * pady
    cv = tk.Canvas(parent, width=w, height=h, bg=parent["bg"], highlightthickness=0)
    draw_rounded_rect(cv, 1, 1, w - 1, h - 1, radius=radius, fill=bg, outline="")
    cv.create_text(w / 2, h / 2, text=text, fill=fg, font=font)
    return cv


def make_rounded_entry(parent, colors: dict, textvariable, show: str | None = None,
                       width: int = 32, font=("Segoe UI", 9), radius: int = 10,
                       padx: int = 10, pady: int = 6) -> tuple[tk.Canvas, tk.Entry]:
    """A rounded-rect Entry. Explicitly sets disabled colors — Tkinter's default
    disabled-state Entry ignores `bg` on Windows and renders a jarring white box."""
    cv = tk.Canvas(parent, bg=parent["bg"], highlightthickness=0)

    # The Entry must be a child of the Canvas that visually embeds it — making
    # it a child of `parent` instead (the bug here) leaves clicks/keystrokes
    # not reliably routed to the widget, since Tk's embedded-window event
    # delivery assumes the embedded widget is actually parented to the canvas.
    entry = tk.Entry(cv, textvariable=textvariable, show=show or "",
                     width=width, font=font, relief=tk.FLAT, bd=0,
                     bg=colors["bg_input"], fg=colors["text_primary"],
                     insertbackground=colors["text_primary"],
                     disabledbackground=colors["bg_input"],
                     disabledforeground=colors["text_ghost"],
                     highlightthickness=0)
    entry.update_idletasks()
    w = entry.winfo_reqwidth() + 2 * padx
    h = entry.winfo_reqheight() + 2 * pady
    cv.configure(width=w, height=h)
    rect_tag = draw_rounded_rect(cv, 1, 1, w - 1, h - 1, radius=radius,
                                fill=colors["bg_input"], outline=colors["border"])
    cv.create_window(padx, pady, anchor="nw", window=entry)

    def set_state(enabled: bool):
        entry.configure(state=(tk.NORMAL if enabled else tk.DISABLED))
        set_rounded_rect_outline(cv, rect_tag, colors["border"] if enabled else "")

    cv.set_state = set_state
    cv.entry = entry
    return cv, entry


# ────────────────────────────────────────────────
#  Chat session (one per tab — one ticket/conversation thread)
# ────────────────────────────────────────────────
class ChatSession:
    """Owns one conversation's state and UI (one Notebook tab)."""

    def __init__(self, app: "ITSupportApp", session_id: str,
                conversation_history: list[dict] | None = None,
                display_log: list[dict] | None = None,
                last_ticket: str | None = None,
                title: str | None = None):
        self.app = app
        self.id = session_id
        self.conversation_history: list[dict] = conversation_history or []
        self.display_log: list[dict] = display_log or []
        self.last_ticket: str | None = last_ticket
        self.title: str = title or "New Ticket"

        self.is_loading: bool = False
        self._anim_id = None
        self._stream_txt = None
        self._stream_outer = None
        self._stream_ts = None
        self._stream_latest_text = ""
        self._stream_render_pending = False

        self.frame: tk.Frame | None = None
        self.canvas: tk.Canvas | None = None
        self.msgs_frame: tk.Frame | None = None
        self.empty_state: tk.Frame | None = None
        self.input_box: tk.Text | None = None
        self.send_canvas: tk.Canvas | None = None
        self.status_var = tk.StringVar(value="")
        self.web_search_var = tk.BooleanVar(
            value=app.config.get("web_search_enabled", False))

        self.pending_image: dict | None = None   # {"media_type", "data"} once attached
        self.preview_row: tk.Frame | None = None
        self._preview_photo = None   # keep a PhotoImage ref alive — Tk drops GC'd images
        self._card_width: int | None = None
        self._card_canvas: tk.Canvas | None = None

    # ── UI construction ───────────────────────────
    def build_ui(self, notebook: ttk.Notebook) -> tk.Frame:
        colors = self.app.colors
        frame = tk.Frame(notebook, bg=colors["bg_dark"])
        frame.session = self
        self.frame = frame

        self._build_chat_canvas(frame)
        self._build_input_bar(frame)

        # A pending image only makes sense for a vision-capable provider/model —
        # if that changed (which rebuilds every session's UI, landing here)
        # while one was attached, drop it rather than silently keep an
        # un-sendable attachment around.
        if self.pending_image and not self._supports_vision():
            self.pending_image = None
        self._show_image_preview()
        return frame

    def _build_chat_canvas(self, parent):
        colors = self.app.colors
        wrap = tk.Frame(parent, bg=colors["bg_dark"])
        wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(wrap, bg=colors["bg_dark"], highlightthickness=0, bd=0)
        vbar = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)

        self.msgs_frame = tk.Frame(self.canvas, bg=colors["bg_dark"])
        self._msgs_win = self.canvas.create_window((0, 0), window=self.msgs_frame, anchor="nw")

        self.canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.msgs_frame.bind("<Configure>", self._on_frame_cfg)
        self.canvas.bind("<Configure>", self._on_canvas_cfg)
        # Bind per-widget (not bind_all) — multiple sessions each have their own
        # canvas, and bind_all would make every tab's wheel scroll the last-built one.
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.msgs_frame.bind("<MouseWheel>", self._on_wheel)

        self._build_empty_state(wrap)

    def _build_empty_state(self, parent):
        """A centered hero shown in place of the chat history when a ticket is empty."""
        colors = self.app.colors
        self.empty_state = tk.Frame(parent, bg=colors["bg_dark"])

        tk.Label(self.empty_state, text="▲", font=("Segoe UI", 30),
                 fg=colors["accent_lit"], bg=colors["bg_dark"]).pack()
        tk.Label(self.empty_state, text="IT Support Assistant",
                 font=("Segoe UI", 24, "bold"), fg=colors["accent_lit"],
                 bg=colors["bg_dark"]).pack(pady=(4, 0))
        tk.Label(self.empty_state, text="Yours for every ticket.",
                 font=("Segoe UI", 11), fg=colors["text_dim"],
                 bg=colors["bg_dark"]).pack(pady=(6, 22))
        tk.Label(self.empty_state,
                 text="Tip: describe your issue below, or click a quick action on the "
                      "left.\nOpen another ticket anytime with ➕ New Ticket above.",
                 font=("Segoe UI", 9), fg=colors["text_ghost"],
                 bg=colors["bg_dark"], justify=tk.CENTER).pack()

        if not self.display_log:
            self._show_empty_state()

    def _show_empty_state(self):
        if self.empty_state is not None:
            self.empty_state.place(relx=0.5, rely=0.4, anchor="center")
            self.empty_state.lift()

    def _hide_empty_state(self):
        if self.empty_state is not None:
            self.empty_state.place_forget()

    def _build_input_bar(self, parent):
        colors = self.app.colors
        outer = tk.Frame(parent, bg=colors["bg_dark"])
        outer.pack(fill=tk.X, side=tk.BOTTOM, padx=20, pady=(0, 16))

        # A rounded "floating card": draw the rounded panel on a Canvas, then embed
        # the real content Frame on top (Tkinter Frames can't have round corners).
        card_canvas = tk.Canvas(outer, bg=colors["bg_dark"], highlightthickness=0)
        card_canvas.pack(fill=tk.X)
        self._card_canvas = card_canvas

        content = tk.Frame(card_canvas, bg=colors["bg_panel"])

        # Reserved, normally-empty row for an attached-image preview — populated
        # by _show_image_preview() only when an image is pending.
        self.preview_row = tk.Frame(content, bg=colors["bg_panel"])
        self.preview_row.pack(fill=tk.X, padx=14, pady=(10, 0))

        top_row = tk.Frame(content, bg=colors["bg_panel"])
        top_row.pack(fill=tk.X, padx=14, pady=(12, 0))

        self.input_box = tk.Text(top_row, height=3,
                                 bg=colors["bg_panel"], fg=colors["text_primary"],
                                 insertbackground=colors["text_primary"],
                                 relief=tk.FLAT, font=("Segoe UI", 11),
                                 padx=0, pady=4, wrap=tk.WORD, bd=0)
        self.input_box.pack(fill=tk.BOTH, expand=True)

        self._placeholder = "Describe your IT issue… (Enter to send · Shift+Enter for new line)"
        self.input_box.insert("1.0", self._placeholder)
        self.input_box.configure(fg=colors["text_ghost"])
        self.input_box.bind("<FocusIn>",  self._focus_in)
        self.input_box.bind("<FocusOut>", self._focus_out)
        self.input_box.bind("<Return>",   self._on_enter)

        bottom_row = tk.Frame(content, bg=colors["bg_panel"])
        bottom_row.pack(fill=tk.X, padx=14, pady=(4, 10))

        web_search_cb = tk.Checkbutton(
                       bottom_row, text="🌐 Web Search", variable=self.web_search_var,
                       font=("Segoe UI", 8), fg=colors["text_dim"],
                       bg=colors["bg_panel"], activebackground=colors["bg_panel"],
                       selectcolor=colors["bg_input"], relief=tk.FLAT,
                       cursor="hand2")
        web_search_cb.pack(side=tk.LEFT)
        add_tooltip(web_search_cb,
                   "When on, your next message is enriched with live web results\n"
                   "before being sent — only for this tab, only for that one message.")

        vision_ok = self._supports_vision()
        attach_btn = tk.Label(bottom_row, text="📎", font=("Segoe UI", 11),
                              fg=(colors["text_dim"] if vision_ok else colors["text_ghost"]),
                              bg=colors["bg_panel"],
                              cursor=("hand2" if vision_ok and PIL_AVAILABLE else "arrow"))
        attach_btn.pack(side=tk.LEFT, padx=(10, 0))
        if vision_ok and PIL_AVAILABLE:
            attach_btn.bind("<Button-1>", lambda e: self._attach_image())
            add_tooltip(attach_btn, "Attach a screenshot for the assistant to look at\n"
                                    "(e.g. an error dialog or BSOD)")
        elif not PIL_AVAILABLE:
            add_tooltip(attach_btn, "Image attachments need Pillow — pip install Pillow")
        elif self.app.settings["provider"] == "ollama":
            add_tooltip(attach_btn, "This Ollama model doesn't support images — pull a "
                                    "vision model (e.g. llama3.2-vision, llava, gemma3) "
                                    "and select it in Settings (⚙️)")
        else:
            add_tooltip(attach_btn, "Image attachments work with Claude or a "
                                    "vision-capable Ollama model — switch in Settings (⚙️)")

        tk.Label(bottom_row, textvariable=self.status_var,
                 font=("Segoe UI", 8), fg=colors["text_dim"],
                 bg=colors["bg_panel"]).pack(side=tk.LEFT, padx=(10, 0))

        self.send_canvas = self._make_round_button(
            bottom_row, diameter=30, command=self.send, glyph="➤")
        self.send_canvas.pack(side=tk.RIGHT)
        add_tooltip(self.send_canvas, "Send  (Enter)")

        card_pad = 2

        def redraw_card(width=None):
            content.update_idletasks()
            w = width if width is not None else (self._card_width or content.winfo_reqwidth())
            self._card_width = w
            h = content.winfo_reqheight()
            draw_rounded_rect(
                card_canvas, 1, 1, w + 2 * card_pad, h + 2 * card_pad, radius=18,
                fill=colors["bg_panel"], outline=colors["border"], tag=self._card_rect_tag)
            card_canvas.tag_lower(self._card_rect_tag, self._card_win_id)
            card_canvas.configure(width=w + 2 * card_pad, height=h + 2 * card_pad)
            card_canvas.itemconfig(self._card_win_id, width=w)

        self._card_rect_tag = draw_rounded_rect(
            card_canvas, 1, 1, 10, 10, radius=18,
            fill=colors["bg_panel"], outline=colors["border"])
        self._card_win_id = card_canvas.create_window(
            card_pad, card_pad, anchor="nw", window=content)
        card_canvas.tag_lower(self._card_rect_tag, self._card_win_id)
        self._redraw_card = redraw_card
        redraw_card()

        def on_resize(event):
            redraw_card(width=max(event.width - 2 * card_pad, 50))

        card_canvas.bind("<Configure>", on_resize)

    def _make_round_button(self, parent, diameter: int, command, glyph: str) -> tk.Canvas:
        colors = self.app.colors
        cv = tk.Canvas(parent, width=diameter, height=diameter,
                       bg=colors["bg_panel"], highlightthickness=0, cursor="hand2")
        oval = cv.create_oval(1, 1, diameter - 1, diameter - 1,
                              fill=colors["send_btn"], outline="")
        label = cv.create_text(diameter // 2, diameter // 2, text=glyph,
                               fill="white", font=("Segoe UI", 12, "bold"))
        cv._oval_id = oval
        cv._label_id = label
        cv._enabled = True

        def handle_click(_event=None):
            if cv._enabled:
                command()

        def on_enter(_event=None):
            if cv._enabled:
                cv.itemconfig(oval, fill=colors["send_hover"])

        def on_leave(_event=None):
            if cv._enabled:
                cv.itemconfig(oval, fill=colors["send_btn"])

        cv.tag_bind(oval, "<Button-1>", handle_click)
        cv.tag_bind(label, "<Button-1>", handle_click)
        cv.tag_bind(oval, "<Enter>", on_enter)
        cv.tag_bind(oval, "<Leave>", on_leave)
        return cv

    def _set_send_enabled(self, enabled: bool):
        cv = self.send_canvas
        if cv is None:
            return
        colors = self.app.colors
        cv._enabled = enabled
        cv.itemconfig(cv._oval_id, fill=colors["send_btn"] if enabled else colors["text_ghost"])
        cv.configure(cursor="hand2" if enabled else "arrow")

    # ── Image attachment ──────────────────────────
    def _supports_vision(self) -> bool:
        provider = self.app.settings["provider"]
        if provider == "claude":
            return True
        if provider == "ollama":
            return "vision" in get_ollama_model_capabilities(self.app.settings.get("ollama_model", ""))
        return False

    def _attach_image(self):
        if not self._supports_vision():
            messagebox.showinfo(
                "Image Attachments",
                "Image attachments need Claude or a vision-capable Ollama model "
                "(e.g. llama3.2-vision, llava, gemma3) — switch in Settings (⚙️) first.")
            return
        path = filedialog.askopenfilename(title="Attach an image", filetypes=IMAGE_FILETYPES)
        if not path:
            return
        try:
            encoded = encode_image_for_api(path)
        except Exception as exc:    # noqa: BLE001 — any bad/corrupt file shouldn't crash the app
            messagebox.showerror("Image Attachments", f"Couldn't read that image: {exc}")
            return
        self.pending_image = encoded
        self._show_image_preview()

    def _clear_pending_image(self):
        self.pending_image = None
        self._preview_photo = None
        self._show_image_preview()

    def _show_image_preview(self):
        colors = self.app.colors
        for w in self.preview_row.winfo_children():
            w.destroy()

        if self.pending_image:
            self._preview_photo = make_thumbnail_photo(self.pending_image["data"], max_size=56)
            thumb = tk.Label(self.preview_row, image=self._preview_photo, bg=colors["bg_panel"])
            thumb.pack(side=tk.LEFT, pady=(0, 8))

            tk.Label(self.preview_row, text="Image attached — sent with your next message",
                     font=("Segoe UI", 8), fg=colors["text_dim"],
                     bg=colors["bg_panel"]).pack(side=tk.LEFT, padx=(8, 0), pady=(0, 8))

            remove_lbl = tk.Label(self.preview_row, text="✕ Remove", font=("Segoe UI", 8),
                                  fg=colors["accent_lit"], bg=colors["bg_panel"], cursor="hand2")
            remove_lbl.pack(side=tk.LEFT, padx=12, pady=(0, 8))
            remove_lbl.bind("<Button-1>", lambda e: self._clear_pending_image())

        # Deferred (after_idle) so the row's new children are laid out before we
        # measure for the resize — but a theme/provider-switch rebuild can destroy
        # this exact card_canvas before that idle callback fires, so it must
        # re-check the widget is still alive rather than assume it.
        if hasattr(self, "_redraw_card") and self._card_canvas is not None:
            def _safe_redraw():
                if self._card_canvas.winfo_exists():
                    self._redraw_card(width=self._card_width)
            self.app.root.after_idle(_safe_redraw)

    def show_initial(self):
        if self.display_log:
            self._hide_empty_state()
            for entry in self.display_log:
                self.post_message(entry["role"], entry["content"],
                                  entry.get("timestamp", ""), record=False,
                                  image_b64=entry.get("image_b64"))
            self.update_exchange_count()
        else:
            self._show_empty_state()

    # ── Canvas / scroll helpers ───────────────────
    def _on_frame_cfg(self, _):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_cfg(self, event):
        self.canvas.itemconfig(self._msgs_win, width=event.width)

    def _on_wheel(self, event):
        # Sign-based, not magnitude-based: high-precision trackpads/mice send deltas
        # that aren't clean multiples of 120, which made int(delta/120) round to 0
        # and the wheel feel like it randomly "stuck". One notch = one scroll step.
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _is_near_bottom(self) -> bool:
        _, fraction_bottom = self.canvas.yview()
        return fraction_bottom >= 0.98

    def _scroll_bottom(self):
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1.0)

    # ── Placeholder text helpers ──────────────────
    def _focus_in(self, _):
        if self.input_box.get("1.0", "end-1c") == self._placeholder:
            self.input_box.delete("1.0", tk.END)
            self.input_box.configure(fg=self.app.colors["text_primary"])

    def _focus_out(self, _):
        if not self.input_box.get("1.0", "end-1c").strip():
            self.input_box.insert("1.0", self._placeholder)
            self.input_box.configure(fg=self.app.colors["text_ghost"])

    # ── Quick-action / clear / copy / export ──────
    def quick_send(self, prompt: str):
        self.input_box.configure(fg=self.app.colors["text_primary"])
        self.input_box.delete("1.0", tk.END)
        self.input_box.insert("1.0", prompt)
        self.send()

    def clear(self):
        if messagebox.askyesno("Clear Ticket",
                               "Clear this ticket's conversation and start fresh?"):
            self.conversation_history.clear()
            self.display_log.clear()
            self.last_ticket = None
            self.title = "New Ticket"
            self.app.notebook.tab(self.frame, text=f"  {self.title}  ")
            self.app.persist_all_history()
            for w in self.msgs_frame.winfo_children():
                w.destroy()
            self.update_exchange_count()
            self._show_empty_state()

    def copy_conversation(self):
        if not self.display_log:
            messagebox.showinfo("Copy Conversation", "No conversation to copy yet.")
            return
        text = self.format_transcript()
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(text)
        self.status_var.set("✅ Conversation copied to clipboard")
        self.app.root.after(3000, lambda: self.status_var.set(""))

    def copy_last_ticket(self):
        if not self.last_ticket:
            messagebox.showinfo("Copy Ticket", "No ticket has been generated on this tab yet.")
            return
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(self.last_ticket)
        self.status_var.set(f"✅ Ticket {self.last_ticket} copied")
        self.app.root.after(3000, lambda: self.status_var.set(""))

    def format_transcript(self) -> str:
        lines = []
        for m in self.display_log:
            speaker = "You" if m["role"] == "user" else "IT Assistant"
            ts = f" ({m['timestamp']})" if m.get("timestamp") else ""
            lines.append(f"{speaker}{ts}: {m['content']}")
        return "\n\n".join(lines)

    def export_chat(self):
        if not self.display_log:
            messagebox.showinfo("Export Chat", "No conversation to export yet.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("JSON file", "*.json")],
            initialfile="it_support_chat.txt")
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.display_log, f, indent=2, ensure_ascii=False)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.format_transcript())
            self.status_var.set(f"✅ Chat exported to {os.path.basename(path)}")
            self.app.root.after(3000, lambda: self.status_var.set(""))
        except OSError as exc:
            messagebox.showerror("Export Failed", f"Could not save file: {exc}")

    # ── Send message ──────────────────────────────
    def _on_enter(self, event):
        if not (event.state & 0x1):   # Shift not held
            self.send()
            return "break"

    def send(self):
        if self.is_loading:
            return

        raw_text = self.input_box.get("1.0", "end-1c").strip()
        text = "" if raw_text == self._placeholder else raw_text
        if not text and not self.pending_image:
            return

        provider = self.app.settings["provider"]
        if provider in ("claude", "deepseek") and not self.app.api_keys.get(provider):
            messagebox.showwarning(
                "API Key Required",
                f"Please enter your {PROVIDER_LABELS[provider]} API key in the header first.")
            return

        if self.pending_image and not self._supports_vision():
            messagebox.showwarning(
                "Image Attachments",
                "This provider/model doesn't support images. Remove the attached "
                "image or switch to Claude / a vision-capable Ollama model in "
                "Settings (⚙️) before sending.")
            return

        self.input_box.delete("1.0", tk.END)
        self.input_box.configure(fg=self.app.colors["text_primary"])

        self._hide_empty_state()
        ts = datetime.now().strftime("%H:%M")

        image_for_send = self.pending_image
        display_text = text or "(screenshot attached)"
        self.post_message("user", display_text, ts,
                          image_b64=(image_for_send["data"] if image_for_send else None))

        if image_for_send:
            prompt_text = text or ("What's shown in this screenshot? Help me understand "
                                   "and fix the issue.")
            if self.app.settings["provider"] == "ollama":
                # Ollama's multimodal format: images is a sibling field next to a
                # plain-string content, not Anthropic's content-block list.
                self.conversation_history.append({
                    "role": "user", "content": prompt_text,
                    "images": [image_for_send["data"]],
                })
            else:
                self.conversation_history.append({"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": image_for_send["media_type"],
                                                 "data": image_for_send["data"]}},
                    {"type": "text", "text": prompt_text},
                ]})
            self._clear_pending_image()
        else:
            self.conversation_history.append({"role": "user", "content": text})

        self.update_exchange_count()
        self.update_title()

        self.is_loading = True
        self._set_send_enabled(False)
        self.status_var.set("⏳ Thinking…")
        self.show_loader()

        threading.Thread(target=self.api_call, daemon=True).start()

    # ── Request context augmentation (KB + web search) ──
    @staticmethod
    def _extract_text(content) -> str:
        """content is normally a string, but is a list of blocks when an image is
        attached (Anthropic's multimodal format) — pull just the text part out."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        return ""

    def build_request_messages(self) -> list[dict]:
        messages = list(self.conversation_history)
        if not (messages and messages[-1]["role"] == "user"):
            return messages

        last_content = messages[-1]["content"]
        query = self._extract_text(last_content)
        extra_context = ""

        kb_matches = search_knowledge_base(query, self.app.kb)
        if kb_matches:
            extra_context += (
                f"\n\n[Internal knowledge base — company-specific procedures, use if "
                f"relevant]:\n{format_kb_results(kb_matches)}")

        if self.web_search_var.get():
            self.app.root.after(0, lambda: self.status_var.set("🔎 Searching the web…"))
            results = web_search(query)
            extra_context += (
                f"\n\n[Live web search results — use only if relevant, and cite "
                f"sources by URL]:\n{format_search_results(results)}")
            self.app.root.after(0, lambda: self.status_var.set("⏳ Thinking…"))

        if not extra_context:
            return messages

        if isinstance(last_content, str):
            # Preserve sibling keys (e.g. Ollama's "images" field sits next to a
            # plain-string content) — don't construct a bare-bones dict that
            # silently drops them.
            messages[-1] = {**messages[-1], "content": query + extra_context}
        else:
            # List content (image attached) — append onto the existing text block
            # rather than replacing the whole thing, so the image block survives.
            new_blocks = []
            appended = False
            for block in last_content:
                if isinstance(block, dict) and block.get("type") == "text" and not appended:
                    new_blocks.append({"type": "text", "text": block.get("text", "") + extra_context})
                    appended = True
                else:
                    new_blocks.append(block)
            if not appended:
                new_blocks.append({"type": "text", "text": extra_context})
            messages[-1] = {"role": "user", "content": new_blocks}
        return messages

    # ── Provider-specific streaming generators ────
    # Both yield ("text", chunk) for streamed text, and (Claude only) ("tool_call",
    # {"id":..., "name":..., "input": {...}}) when the model wants to call a tool.
    def stream_claude(self, messages_for_request: list[dict], tools: list | None = None):
        headers = {
            "x-api-key":         self.app.api_keys.get("claude", ""),
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        }
        payload = {
            "model":       self.app.settings["model"],
            "system":      SYSTEM_PROMPT,
            "messages":    messages_for_request,
            "max_tokens":  self.app.settings["max_tokens"],
            "temperature": self.app.settings["temperature"],
            "stream":      True,
        }
        if tools:
            payload["tools"] = tools

        resp = requests.post(
            CLAUDE_URL, headers=headers, json=payload, timeout=60, stream=True)

        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After", "60")
            raise RuntimeError(f"Rate limit reached. Please wait ~{retry}s and try again.")
        if resp.status_code == 401:
            raise RuntimeError("Invalid API key. Double-check your Anthropic API key.")
        if resp.status_code == 529:
            raise RuntimeError("Claude API is overloaded. Please try again shortly.")
        resp.raise_for_status()

        current_tool = None
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if not data_str:
                continue
            try:
                event = json.loads(data_str)
            except (json.JSONDecodeError, ValueError):
                continue

            etype = event.get("type")
            if etype == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    current_tool = {"id": block.get("id"), "name": block.get("name"),
                                    "input_json": ""}
            elif etype == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    chunk = delta.get("text", "")
                    if chunk:
                        yield ("text", chunk)
                elif delta.get("type") == "input_json_delta" and current_tool is not None:
                    current_tool["input_json"] += delta.get("partial_json", "")
            elif etype == "content_block_stop":
                if current_tool is not None:
                    try:
                        parsed = json.loads(current_tool["input_json"]) \
                                if current_tool["input_json"] else {}
                    except (json.JSONDecodeError, ValueError):
                        parsed = {}
                    yield ("tool_call", {"id": current_tool["id"],
                                         "name": current_tool["name"], "input": parsed})
                    current_tool = None
            elif etype == "error":
                raise RuntimeError(event.get("error", {}).get("message", "Streaming error."))
            elif etype == "message_stop":
                break

    @staticmethod
    def _ollama_messages_with_system(messages: list[dict]) -> list[dict]:
        """
        Most Ollama models handle a separate {"role": "system", ...} message
        fine, but some small/specialized ones (moondream confirmed) have a chat
        template with no system slot at all — Ollama silently drops that message
        entirely, so the model never sees the IT-support persona, instructions,
        or tool context (confirmed via prompt_eval_count staying identical
        whether or not the system message was included). Folding the prompt
        into the first user turn instead works universally, since every
        template necessarily renders the user's own content.
        """
        if not messages or messages[0].get("role") != "user":
            return [{"role": "system", "content": SYSTEM_PROMPT}] + messages

        folded = list(messages)
        first = folded[0]
        content = first.get("content")
        prefix = SYSTEM_PROMPT + "\n\n---\n\n"

        if isinstance(content, str):
            folded[0] = {**first, "content": prefix + content}
        elif isinstance(content, list):
            new_blocks = []
            prefixed = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and not prefixed:
                    new_blocks.append({"type": "text", "text": prefix + block.get("text", "")})
                    prefixed = True
                else:
                    new_blocks.append(block)
            if not prefixed:
                new_blocks.insert(0, {"type": "text", "text": prefix})
            folded[0] = {**first, "content": new_blocks}
        else:
            return [{"role": "system", "content": SYSTEM_PROMPT}] + messages

        return folded

    def stream_ollama(self, messages_for_request: list[dict], tools: list | None = None):
        payload = {
            "model": self.app.settings["ollama_model"],
            "messages": self._ollama_messages_with_system(messages_for_request),
            "stream": True,
            "options": {"temperature": self.app.settings["temperature"]},
        }
        if tools:
            payload["tools"] = [to_ollama_tool_format(t) for t in tools]

        try:
            resp = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=180, stream=True)
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                "Could not reach Ollama at localhost:11434. Is `ollama serve` running?"
            ) from exc

        if resp.status_code == 404:
            raise RuntimeError(
                f"Model '{self.app.settings['ollama_model']}' isn't pulled locally. "
                f"Run: ollama pull {self.app.settings['ollama_model']}")
        resp.raise_for_status()

        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("error"):
                raise RuntimeError(obj["error"])
            message = obj.get("message", {})
            chunk = message.get("content", "")
            if chunk:
                yield ("text", chunk)
            # Ollama returns tool calls whole (not as incremental JSON deltas
            # like Anthropic) — each has no stable id, so we generate one to
            # plug into the same tool-result bookkeeping the Claude path uses.
            for tc in message.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                yield ("tool_call", {
                    "id": f"ollama_call_{uuid.uuid4().hex[:8]}",
                    "name": fn.get("name", ""),
                    "input": fn.get("arguments", {}),
                })
            if obj.get("done"):
                break

    def stream_deepseek(self, messages_for_request: list[dict]):
        """Yields text chunks from DeepSeek's OpenAI-compatible SSE stream."""
        headers = {
            "Authorization": f"Bearer {self.app.api_keys.get('deepseek', '')}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model":       self.app.settings["deepseek_model"],
            "messages":    [{"role": "system", "content": SYSTEM_PROMPT}] + messages_for_request,
            "max_tokens":  self.app.settings["max_tokens"],
            "temperature": self.app.settings["temperature"],
            "stream":      True,
        }

        resp = requests.post(
            DEEPSEEK_URL, headers=headers, json=payload, timeout=60, stream=True)

        if resp.status_code == 429:
            raise RuntimeError("Rate limit reached. Please wait and try again.")
        if resp.status_code == 401:
            raise RuntimeError("Invalid API key. Double-check your DeepSeek API key.")
        resp.raise_for_status()

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if not data_str or data_str == "[DONE]":
                continue
            try:
                event = json.loads(data_str)
            except (json.JSONDecodeError, ValueError):
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            chunk = delta.get("content", "")
            if chunk:
                yield ("text", chunk)

    # ── API call (streamed, provider-agnostic, tool-use loop) ──
    def api_call(self):
        """
        Streams a reply from the selected provider. For Claude with tool use enabled,
        loops: stream a turn, execute any requested tools, feed results back, repeat
        (capped at MAX_TOOL_ITERATIONS) until the model produces a final answer with
        no further tool calls. Only the final persisted text goes into
        conversation_history — tool_use/tool_result blocks are ephemeral request
        context, same pattern as the web-search/KB augmentation.
        """
        full_reply = ""
        started = False
        try:
            messages_for_request = self.build_request_messages()
            provider = self.app.settings["provider"]
            ollama_caps = get_ollama_model_capabilities(self.app.settings.get("ollama_model", ""))
            tool_capable = provider == "claude" or (provider == "ollama" and "tools" in ollama_caps)
            use_tools = tool_capable and self.app.settings.get("tools_enabled", True)
            active_tools = TOOL_DEFS + (
                [DIAGNOSTIC_TOOL_DEF] if self.app.settings.get("diagnostics_enabled", False) else [])

            for _ in range(MAX_TOOL_ITERATIONS):
                tool_calls_this_round = []
                assistant_text_this_round = ""

                if use_tools and provider == "claude":
                    stream_iter = self.stream_claude(messages_for_request, tools=active_tools)
                elif use_tools and provider == "ollama":
                    stream_iter = self.stream_ollama(messages_for_request, tools=active_tools)
                elif provider == "claude":
                    stream_iter = self.stream_claude(messages_for_request)
                elif provider == "deepseek":
                    stream_iter = self.stream_deepseek(messages_for_request)
                else:
                    stream_iter = self.stream_ollama(messages_for_request)

                for kind, payload in stream_iter:
                    if kind == "text":
                        if not started:
                            started = True
                            self.app.root.after(0, self.begin_stream)
                        full_reply += payload
                        assistant_text_this_round += payload
                        self.app.root.after(0, self.update_stream, full_reply)
                    elif kind == "tool_call":
                        tool_calls_this_round.append(payload)

                if not tool_calls_this_round:
                    break

                tool_names = ", ".join(t["name"] for t in tool_calls_this_round)
                self.app.root.after(
                    0, lambda n=tool_names: self.status_var.set(f"🔧 Checking: {n}…"))

                # Execute tools — same functions/results regardless of provider;
                # only the message shape fed back differs below.
                tool_results = {}
                for tc in tool_calls_this_round:
                    try:
                        if tc["name"] == "search_internal_kb":
                            # Needs the app's live, user-editable KB instance —
                            # unlike the other tools, this can't be a plain
                            # module-level function with no app context.
                            query = tc["input"].get("query", "")
                            matches = search_knowledge_base(query, self.app.kb)
                            result = {
                                "query": query,
                                "results": [{"title": m["title"], "content": m["content"]}
                                           for m in matches],
                            }
                            if not matches:
                                result["note"] = "no matching internal articles"
                        else:
                            fn = TOOL_FUNCTIONS.get(tc["name"])
                            result = fn(tc["input"]) if fn else {
                                "error": f"Unknown tool {tc['name']}"}
                    except Exception as exc:    # noqa: BLE001 — tool failures shouldn't crash the chat
                        result = {"error": str(exc)}
                    tool_results[tc["id"]] = result

                if provider == "claude":
                    assistant_content = []
                    if assistant_text_this_round:
                        assistant_content.append(
                            {"type": "text", "text": assistant_text_this_round})
                    for tc in tool_calls_this_round:
                        assistant_content.append({"type": "tool_use", "id": tc["id"],
                                                  "name": tc["name"], "input": tc["input"]})
                    messages_for_request = messages_for_request + [
                        {"role": "assistant", "content": assistant_content}]

                    tool_result_blocks = [
                        {"type": "tool_result", "tool_use_id": tc["id"],
                         "content": json.dumps(tool_results[tc["id"]])}
                        for tc in tool_calls_this_round]
                    messages_for_request = messages_for_request + [
                        {"role": "user", "content": tool_result_blocks}]
                else:
                    # Ollama's tool-result protocol: echo the assistant's
                    # tool_calls, then one "tool"-role message per result —
                    # matched by the model positionally/by name, no id needed.
                    ollama_tool_calls = [
                        {"function": {"name": tc["name"], "arguments": tc["input"]}}
                        for tc in tool_calls_this_round]
                    messages_for_request = messages_for_request + [
                        {"role": "assistant", "content": assistant_text_this_round,
                         "tool_calls": ollama_tool_calls}]
                    for tc in tool_calls_this_round:
                        messages_for_request = messages_for_request + [
                            {"role": "tool", "content": json.dumps(tool_results[tc["id"]]),
                             "name": tc["name"]}]

                self.app.root.after(0, lambda: self.status_var.set("⏳ Thinking…"))

            if not full_reply:
                raise RuntimeError("Unexpected response — no content returned.")

            self.conversation_history.append({"role": "assistant", "content": full_reply})
            self.app.root.after(0, self.on_success, full_reply, started)

        except requests.exceptions.ConnectionError:
            self.app.root.after(0, self.on_error,
                "⚠️ Network Error: Cannot reach the API endpoint.\n"
                "Please check your internet connection and try again.", started)

        except requests.exceptions.Timeout:
            self.app.root.after(0, self.on_error,
                "⚠️ Timeout: The server took too long to respond.\n"
                "The service may be busy — please try again in a moment.", started)

        except requests.exceptions.HTTPError as exc:
            self.app.root.after(0, self.on_error,
                f"⚠️ HTTP Error: {exc}\nPlease try again.", started)

        except RuntimeError as exc:
            self.app.root.after(0, self.on_error, f"⚠️ {exc}", started)

        except Exception as exc:          # noqa: BLE001  — catch-all safety net
            self.app.root.after(0, self.on_error,
                f"⚠️ Unexpected error: {exc}", started)

    # ── Streaming bubble lifecycle (run in main thread) ──
    def begin_stream(self):
        self.hide_loader()
        self._hide_empty_state()
        self._stream_ts = datetime.now().strftime("%H:%M")
        self._stream_outer, self._stream_txt = self.make_bubble("assistant", self._stream_ts)
        self._stream_latest_text = ""
        self._stream_render_pending = False

    def update_stream(self, text_so_far: str):
        # SSE can deliver many small chunks per second. Re-parsing markdown and
        # forcing a layout pass on every single one made long replies feel janky.
        # Coalesce bursts into one render every ~40ms (still feels live) instead.
        self._stream_latest_text = text_so_far
        if self._stream_txt is not None and not self._stream_render_pending:
            self._stream_render_pending = True
            self.app.root.after(40, self._flush_stream_render)

    def _flush_stream_render(self):
        self._stream_render_pending = False
        if self._stream_txt is None:
            return
        was_near_bottom = self._is_near_bottom()
        self.render_bubble_text(self._stream_txt, self._stream_latest_text, markdown=True)
        if was_near_bottom:
            self._scroll_bottom()

    def discard_stream(self):
        self.hide_loader()
        self._stream_render_pending = False
        if self._stream_txt is not None:
            if self._stream_outer.winfo_exists():
                self._stream_outer.destroy()
            self._stream_outer = None
            self._stream_txt = None

    # ── Success / error callbacks (run in main thread) ──
    def on_success(self, reply: str, was_streamed: bool):
        if was_streamed and self._stream_txt is not None:
            # The throttled render may be lagging behind the final text at this
            # instant — force one last render so the bubble never ends up showing
            # a stale, truncated version of the reply.
            was_near_bottom = self._is_near_bottom()
            self.render_bubble_text(self._stream_txt, reply, markdown=True)
            if was_near_bottom:
                self._scroll_bottom()
            self.display_log.append({
                "role": "assistant", "content": reply, "timestamp": self._stream_ts})
            self._stream_txt = None
            self._stream_render_pending = False
        else:
            self.hide_loader()
            ts = datetime.now().strftime("%H:%M")
            self.post_message("assistant", reply, ts)

        self.track_tickets_in_reply(reply)
        self.update_exchange_count()
        self.update_title()
        self.app.persist_all_history()
        self.status_var.set("")
        self.reset_send_btn()

    def on_error(self, msg: str, was_streamed: bool):
        self.discard_stream()
        issue_summary = ""
        if self.conversation_history and self.conversation_history[-1]["role"] == "user":
            # _extract_text handles both plain-string content and the list-of-blocks
            # form used when an image is attached — sqlite can't bind a list.
            issue_summary = self._extract_text(self.conversation_history[-1]["content"])
            self.conversation_history.pop()
            self.update_exchange_count()

        ticket = create_ticket(issue_summary or msg)
        self.last_ticket = ticket
        self.update_title()
        self.update_exchange_count()
        full = (f"{msg}\n\n"
                f"📋 If this persists, contact IT directly.\n"
                f"Reference ticket: {ticket}")
        ts = datetime.now().strftime("%H:%M")
        self.post_message("assistant", full, ts)
        self.app.persist_all_history()
        self.status_var.set("❌ Request failed — you can try again")
        self.reset_send_btn()

    def track_tickets_in_reply(self, reply: str) -> None:
        matches = TICKET_PATTERN.findall(reply)
        if not matches:
            return
        issue_summary = ""
        for m in reversed(self.conversation_history):
            if m["role"] == "user":
                issue_summary = self._extract_text(m["content"])
                break
        for ticket_number in matches:
            record_ticket(ticket_number, issue_summary)
            self.last_ticket = ticket_number

    def reset_send_btn(self):
        self.is_loading = False
        self._set_send_enabled(True)

    # ── Loading indicator (animated dots) ─────────
    def show_loader(self):
        colors = self.app.colors
        self._loader_frame = tk.Frame(self.msgs_frame, bg=colors["bg_dark"])
        self._loader_frame.pack(fill=tk.X, padx=15, pady=6, anchor="w")

        font = ("Segoe UI", 12)
        measurer = tkfont.Font(font=font)
        text0 = "●  ○  ○"
        w = measurer.measure(text0) + 28
        h = measurer.metrics("linespace") + 18

        self._loader_canvas = tk.Canvas(self._loader_frame, width=w, height=h,
                                        bg=colors["bg_dark"], highlightthickness=0)
        self._loader_canvas.pack(side=tk.LEFT)
        draw_rounded_rect(self._loader_canvas, 1, 1, w - 1, h - 1, radius=16,
                          fill=colors["ai_bubble"], outline="")
        self._loader_dots_id = self._loader_canvas.create_text(
            w / 2, h / 2, text=text0, fill=colors["accent_lit"], font=font)

        for w_ in (self._loader_frame, self._loader_canvas):
            w_.bind("<MouseWheel>", self._on_wheel)
        self.app.root.after(100, self._scroll_bottom)
        self._animate(0)

    def _animate(self, frame: int):
        if not (hasattr(self, "_loader_canvas") and self._loader_canvas.winfo_exists()):
            return
        frames = ["●  ○  ○", "○  ●  ○", "○  ○  ●", "○  ●  ○"]
        self._loader_canvas.itemconfig(self._loader_dots_id, text=frames[frame % 4])
        self._anim_id = self.app.root.after(350, self._animate, frame + 1)

    def hide_loader(self):
        if self._anim_id:
            self.app.root.after_cancel(self._anim_id)
            self._anim_id = None
        if hasattr(self, "_loader_frame") and self._loader_frame.winfo_exists():
            self._loader_frame.destroy()

    # ── Post a message bubble ─────────────────────
    def post_message(self, role: str, content: str, timestamp: str = "", record: bool = True,
                     image_b64: str | None = None):
        self._hide_empty_state()
        if record:
            entry = {"role": role, "content": content, "timestamp": timestamp}
            if image_b64:
                entry["image_b64"] = image_b64
            self.display_log.append(entry)

        _, txt = self.make_bubble(role, timestamp, image_b64=image_b64)
        self.render_bubble_text(txt, content, markdown=(role != "user"))
        self.app.root.after(80, self._scroll_bottom)

    def make_bubble(self, role: str, timestamp: str, image_b64: str | None = None):
        colors = self.app.colors
        is_user = (role == "user")

        outer = tk.Frame(self.msgs_frame, bg=colors["bg_dark"])
        outer.pack(fill=tk.X, padx=14, pady=6)

        container = tk.Frame(outer, bg=colors["bg_dark"])
        container.pack(side=tk.RIGHT if is_user else tk.LEFT)

        label_text = (f"You  {timestamp}" if is_user else f"IT Assistant  {timestamp}")
        label_fg   = "#80b3ff" if is_user else colors["accent_lit"]
        label = tk.Label(container, text=label_text,
                 font=("Segoe UI", 8), fg=label_fg,
                 bg=colors["bg_dark"])
        label.pack(anchor="e" if is_user else "w", pady=(0, 3))

        if image_b64 and PIL_AVAILABLE:
            try:
                photo = make_thumbnail_photo(image_b64, max_size=220)
                img_label = tk.Label(container, image=photo, bg=colors["bg_dark"], bd=0)
                img_label.image = photo   # keep a reference alive — Tk drops GC'd images
                img_label.pack(anchor="e" if is_user else "w", pady=(0, 4))
                img_label.bind("<MouseWheel>", self._on_wheel)
            except Exception:    # noqa: BLE001 — a bad thumbnail shouldn't break the chat
                pass

        bubble_color = colors["user_bubble"] if is_user else colors["ai_bubble"]

        # Rounded bubble: draw the rounded background on a Canvas, then embed an
        # inner Frame (holding the Text + an initially-hidden Scrollbar) on top
        # of it (Tkinter has no native border-radius on widgets).
        bubble_canvas = tk.Canvas(container, bg=colors["bg_dark"], highlightthickness=0)
        bubble_canvas.pack()

        bubble_inner = tk.Frame(bubble_canvas, bg=bubble_color)

        txt = tk.Text(bubble_inner, bg=bubble_color, fg=colors["text_primary"],
                      font=("Segoe UI", 10), relief=tk.FLAT, bd=0, highlightthickness=0,
                      wrap=tk.WORD, width=54, cursor="arrow",
                      selectbackground=colors["accent_lit"], selectforeground="white")
        txt.tag_configure("bold", font=("Segoe UI", 10, "bold"))
        txt.tag_configure("heading", font=("Segoe UI", 11, "bold"))
        txt.tag_configure("code", font=("Consolas", 9), background=colors["bg_input"])
        txt.configure(state=tk.DISABLED)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Long replies (web search dumps, diagnostic output, etc.) get capped at
        # BUBBLE_MAX_VISIBLE_LINES tall — without this, that overflow content was
        # only reachable by dragging a text selection past the edge (which
        # auto-scrolls a Text widget even with no scrollbar) since wheel events
        # were unconditionally redirected to scroll the outer conversation
        # instead. Not packed by default; render_bubble_text shows it on overflow.
        scrollbar = ttk.Scrollbar(bubble_inner, orient="vertical", command=txt.yview,
                                  style="Vertical.TScrollbar")
        txt.configure(yscrollcommand=scrollbar.set)

        pad = 13
        rect_tag = draw_rounded_rect(bubble_canvas, 0, 0, 10, 10, radius=14,
                                     fill=bubble_color, outline="")
        win_id = bubble_canvas.create_window(pad, pad, anchor="nw", window=bubble_inner)
        bubble_canvas.tag_lower(rect_tag, win_id)
        txt._bubble_canvas = bubble_canvas
        txt._bubble_inner = bubble_inner
        txt._bubble_scrollbar = scrollbar
        txt._bubble_rect = rect_tag
        txt._bubble_win = win_id
        txt._bubble_pad = pad
        txt._bubble_color = bubble_color
        txt._bubble_overflowing = False

        # A Text widget eats <MouseWheel> for its own (no-op, single-bubble) scrolling
        # by default, so the event never reaches the canvas. Redirect every widget in
        # the bubble explicitly so the wheel scrolls the conversation no matter where
        # the cursor happens to be — except `txt` itself once it's overflowing, where
        # render_bubble_text rebinds the wheel to scroll the bubble's own content.
        for w in (outer, container, label, bubble_canvas):
            w.bind("<MouseWheel>", self._on_wheel)
        txt.bind("<MouseWheel>", self._on_wheel)

        return outer, txt

    BUBBLE_MAX_VISIBLE_LINES = 40

    def render_bubble_text(self, txt: tk.Text, content: str, markdown: bool = True):
        txt.configure(state=tk.NORMAL)
        txt.delete("1.0", tk.END)
        if markdown:
            self._insert_markdown(txt, content)
        else:
            txt.insert("1.0", content)
        txt.configure(state=tk.DISABLED)

        txt.update_idletasks()
        # txt.index(END) counts LOGICAL lines (split on literal "\n"), not the
        # actual visual rows after word-wrap — a single long paragraph with no
        # embedded newlines reports as "1 line" even though wrap=WORD renders
        # it across several rows. Sizing height from that undercounts badly and
        # was cutting off the wrapped tail of nearly every paragraph the bot
        # wrote. count(..., "displaylines") gives the real, wrapped row count.
        line_count = txt.count("1.0", "end", "displaylines")[0]
        overflowing = line_count > self.BUBBLE_MAX_VISIBLE_LINES
        txt.configure(height=min(max(line_count, 1), self.BUBBLE_MAX_VISIBLE_LINES))

        if overflowing and not txt._bubble_overflowing:
            txt._bubble_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            txt.unbind("<MouseWheel>")   # let the Text widget scroll its own content now
            txt._bubble_overflowing = True
        elif not overflowing and txt._bubble_overflowing:
            txt._bubble_scrollbar.pack_forget()
            txt.bind("<MouseWheel>", self._on_wheel)   # back to redirecting to outer scroll
            txt._bubble_overflowing = False

        if overflowing:
            # delete+reinsert above always resets the view to the top — without
            # this, a streaming reply that grows past the visible cap stayed
            # pinned to its first ~40 lines while the rest kept growing out of
            # sight below, so the tail of the message was never visible by
            # default (this was the actual bug, not just "no scrollbar").
            txt.yview_moveto(1.0)

        self._fit_bubble(txt)

    def _fit_bubble(self, txt: tk.Text):
        """Resize the bubble's rounded background to match its inner content."""
        cv = txt._bubble_canvas
        pad = txt._bubble_pad
        txt._bubble_inner.update_idletasks()
        w = txt._bubble_inner.winfo_reqwidth()
        h = txt._bubble_inner.winfo_reqheight()
        cv.configure(width=w + 2 * pad, height=h + 2 * pad)
        draw_rounded_rect(cv, 0, 0, w + 2 * pad, h + 2 * pad, radius=14,
                         fill=txt._bubble_color, outline="", tag=txt._bubble_rect)
        cv.tag_lower(txt._bubble_rect, txt._bubble_win)
        cv.coords(txt._bubble_win, pad, pad)

    # ── Lightweight markdown rendering ────────────
    _INLINE_PATTERN = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")

    def _insert_markdown(self, txt: tk.Text, content: str):
        lines = content.split("\n")
        for i, line in enumerate(lines):
            heading_match = re.match(r"^(#{1,3})\s+(.*)", line)
            bullet_match = re.match(r"^[-*]\s+(.*)", line)
            if heading_match:
                self._insert_inline(txt, heading_match.group(2), extra_tag="heading")
            elif bullet_match:
                self._insert_inline(txt, "•  " + bullet_match.group(1))
            else:
                self._insert_inline(txt, line)
            if i < len(lines) - 1:
                txt.insert(tk.END, "\n")

    def _insert_inline(self, txt: tk.Text, line: str, extra_tag: str | None = None):
        pos = 0
        for m in self._INLINE_PATTERN.finditer(line):
            if m.start() > pos:
                tags = (extra_tag,) if extra_tag else ()
                txt.insert(tk.END, line[pos:m.start()], tags)
            token = m.group(0)
            if token.startswith("**"):
                tags = ("bold",) + ((extra_tag,) if extra_tag else ())
                txt.insert(tk.END, token[2:-2], tags)
            else:
                tags = ("code",) + ((extra_tag,) if extra_tag else ())
                txt.insert(tk.END, token[1:-1], tags)
            pos = m.end()
        if pos < len(line):
            tags = (extra_tag,) if extra_tag else ()
            txt.insert(tk.END, line[pos:], tags)

    # ── Exchange counter / tab title ──────────────
    def update_exchange_count(self):
        if self.app.active_session is self:
            pairs = sum(1 for m in self.conversation_history if m["role"] == "user")
            ticket_part = f"  ·  {self.last_ticket}" if self.last_ticket else ""
            self.app.msg_count_var.set(f"{pairs} exchange{'s' if pairs != 1 else ''}{ticket_part}")

    def update_title(self):
        if self.last_ticket:
            new_title = self.last_ticket
        else:
            first_user_content = next((m["content"] for m in self.conversation_history
                                       if m["role"] == "user"), "")
            first_user = self._extract_text(first_user_content) or "📷 Image"
            new_title = (first_user[:22] + "…") if len(first_user) > 22 else \
                       (first_user or "New Ticket")
        if new_title != self.title:
            self.title = new_title
            if self.frame is not None:
                self.app.notebook.tab(self.frame, text=f"  {self.title}  ")

    # ── Persistence ────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "title":               self.title,
            "conversation_history": self.conversation_history,
            "display_log":         self.display_log,
            "last_ticket":         self.last_ticket,
        }


# ────────────────────────────────────────────────
#  Main Application
# ────────────────────────────────────────────────
class ITSupportApp:
    """AI-Powered IT Support Desktop Application — manages tabs of ChatSessions."""

    @staticmethod
    def _pick_ollama_default() -> str:
        """
        Used only when there's no saved ollama_model in config (fresh install).
        DEFAULT_OLLAMA_MODEL is a reasonable guess, but if the user doesn't
        actually have it pulled, defaulting to it anyway just produces a
        confusing "isn't pulled locally" error on first launch. Prefer it if
        present, otherwise fall back to whatever IS actually pulled locally.
        """
        local_models = fetch_ollama_models()
        if not local_models:
            return DEFAULT_OLLAMA_MODEL
        default_base = DEFAULT_OLLAMA_MODEL.split(":")[0]
        if any(name.split(":")[0] == default_base for name in local_models):
            return DEFAULT_OLLAMA_MODEL
        # Prefer a general-purpose, tool-capable model over a narrow
        # vision-only specialist (e.g. moondream) as the default for chat.
        for name in local_models:
            if "tools" in get_ollama_model_capabilities(name):
                return name
        return local_models[0]

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AI-Powered IT Support  |  Help Desk")
        self.root.geometry("1020x740")
        self.root.minsize(820, 540)

        # Persisted config (API key + theme + model settings)
        self.config: dict = load_config()
        self.theme: str = self.config.get("theme", "dark")
        self.colors: dict = THEMES.get(self.theme, DARK_COLORS)
        self.settings: dict = {
            "provider":       self.config.get("provider", "ollama"),
            "model":          self.config.get("model", CLAUDE_MODEL),
            "ollama_model":   self.config.get("ollama_model") or self._pick_ollama_default(),
            "deepseek_model": self.config.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL),
            "temperature":    self.config.get("temperature", 0.6),
            "max_tokens":     self.config.get("max_tokens", 1500),
            "tools_enabled":  self.config.get("tools_enabled", True),
            "diagnostics_enabled": self.config.get("diagnostics_enabled", False),
        }

        # Separate keys per provider — switching providers shouldn't lose the other's key
        self.api_keys: dict = {
            "claude":   self.config.get("api_key", ""),
            "deepseek": self.config.get("deepseek_api_key", ""),
        }
        self.api_var = tk.StringVar(
            value=self.api_keys.get(self.settings["provider"], ""))

        self.kb: list[dict] = load_knowledge_base()

        self.sessions: list[ChatSession] = []
        self._next_session_id = 0
        self.notebook: ttk.Notebook | None = None

        self.root.configure(bg=self.colors["bg_dark"])
        self._apply_ttk_style()
        self._build_header()
        self._build_body()
        self._init_sessions_from_history()
        self._bind_shortcuts()

    # ── Keyboard shortcuts ─────────────────────────
    def _bind_shortcuts(self):
        self.root.bind_all("<Control-n>", lambda e: self.new_session())
        self.root.bind_all("<Control-w>", lambda e: self.close_active_session())
        self.root.bind_all("<Control-comma>", lambda e: self._open_settings())
        self.root.bind_all("<Control-Key-1>", lambda e: self._select_tab(0))
        self.root.bind_all("<Control-Key-2>", lambda e: self._select_tab(1))
        self.root.bind_all("<Control-Key-3>", lambda e: self._select_tab(2))
        self.root.bind_all("<Control-Tab>", lambda e: self._cycle_tab(1))
        self.root.bind_all("<Control-Shift-Tab>", lambda e: self._cycle_tab(-1))

    def _select_tab(self, index: int):
        if 0 <= index < len(self.sessions):
            self.notebook.select(self.sessions[index].frame)

    def _cycle_tab(self, step: int):
        if not self.sessions:
            return
        current = self.sessions.index(self.active_session)
        self.notebook.select(self.sessions[(current + step) % len(self.sessions)].frame)

    # ── Session bootstrap / persistence ───────────
    def _init_sessions_from_history(self):
        raw = load_history()
        if "sessions" in raw:
            sessions_data = raw["sessions"]
            active_index = raw.get("active_index", 0)
        elif raw.get("conversation_history") or raw.get("display_log"):
            sessions_data = [raw]   # migrate old single-session format
            active_index = 0
        else:
            sessions_data = []
            active_index = 0

        if not sessions_data:
            sessions_data = [{}]

        for sd in sessions_data:
            self._add_session(
                conversation_history=sd.get("conversation_history", []),
                display_log=sd.get("display_log", []),
                last_ticket=sd.get("last_ticket"),
                title=sd.get("title"),
                select=False)

        clamped = max(0, min(active_index, len(self.sessions) - 1))
        self.notebook.select(self.sessions[clamped].frame)
        self._refresh_session_label()

    def _add_session(self, conversation_history=None, display_log=None,
                     last_ticket=None, title=None, select=True) -> "ChatSession":
        session = ChatSession(
            self, session_id=str(self._next_session_id),
            conversation_history=conversation_history, display_log=display_log,
            last_ticket=last_ticket, title=title)
        self._next_session_id += 1
        frame = session.build_ui(self.notebook)
        self.notebook.add(frame, text=f"  {session.title}  ")
        self.sessions.append(session)
        session.show_initial()
        if select:
            self.notebook.select(frame)
        return session

    def new_session(self):
        self._add_session(select=True)
        self.persist_all_history()

    def close_active_session(self):
        session = self.active_session
        if session is None:
            return
        if len(self.sessions) == 1:
            if not messagebox.askyesno(
                    "Close Tab",
                    "This is your only open ticket. Close it and start a fresh one?"):
                return
        self.notebook.forget(session.frame)
        self.sessions.remove(session)
        if not self.sessions:
            self._add_session(select=True)
        self.persist_all_history()
        self._refresh_session_label()

    @property
    def active_session(self):
        if not self.sessions:
            return None
        tab_id = self.notebook.select() if self.notebook else None
        if not tab_id:
            return self.sessions[-1]
        for s in self.sessions:
            if s.frame is not None and str(s.frame) == tab_id:
                return s
        return self.sessions[-1]

    def _on_tab_changed(self, _event=None):
        self._refresh_session_label()

    def _refresh_session_label(self):
        active = self.active_session
        if active is not None:
            active.update_exchange_count()

    def persist_all_history(self):
        active = self.active_session
        save_history({
            "sessions": [s.to_dict() for s in self.sessions],
            "active_index": self.sessions.index(active) if active in self.sessions else 0,
        })

    # ── TTK styling ──────────────────────────────
    def _apply_ttk_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Vertical.TScrollbar",
            background=self.colors["border"],
            troughcolor=self.colors["bg_dark"],
            arrowcolor=self.colors["text_dim"],
            bordercolor=self.colors["bg_dark"],
            gripcount=0,
        )
        style.configure(
            "TNotebook", background=self.colors["bg_dark"], borderwidth=0)
        style.configure(
            "TNotebook.Tab", background=self.colors["bg_panel"],
            foreground=self.colors["text_dim"], padding=(10, 6),
            font=("Segoe UI", 9))
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.colors["accent"])],
            foreground=[("selected", self.colors["text_primary"])])

    # ── Header ───────────────────────────────────
    def _build_header(self):
        # NOTE: pack_propagate(False) makes this height a hard clip boundary —
        # any child whose (height + 2*pady) exceeds it gets its bottom sliced
        # off by the parent's edge. That's what caused the icon buttons' bottom
        # corners to look perfectly flat/square: 36px button + 20px top/bottom
        # padding = 76px, inside what was only a 64px-tall header. Sized this
        # generously (with a matching smaller, uniform pady below) so nothing
        # the header hosts can ever exceed it again.
        HEADER_H = 72
        BTN_PAD_Y = 12

        hdr = tk.Frame(self.root, bg=self.colors["header_bg"], height=HEADER_H)
        hdr.pack(fill=tk.X, side=tk.TOP)
        hdr.pack_propagate(False)

        left = tk.Frame(hdr, bg=self.colors["header_bg"])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=18)

        tk.Label(left, text="▲", font=("Segoe UI", 14),
                 fg=self.colors["accent_lit"], bg=self.colors["header_bg"]
                 ).pack(side=tk.LEFT, pady=BTN_PAD_Y)

        tk.Label(left, text=" IT Support",
                 font=("Segoe UI", 15, "bold"),
                 fg=self.colors["accent_lit"], bg=self.colors["header_bg"]).pack(side=tk.LEFT)

        provider = self.settings["provider"]
        model_name = {"ollama": self.settings["ollama_model"],
                     "claude": self.settings["model"],
                     "deepseek": self.settings["deepseek_model"]}[provider]
        badge = make_pill_label(
            left, f"{PROVIDER_LABELS[provider]} · {model_name}",
            bg=PROVIDER_BADGE_COLOR[provider], fg="white",
            font=("Segoe UI", 9, "bold"), radius=12)
        badge.pack(side=tk.LEFT, padx=(12, 0), pady=BTN_PAD_Y)
        add_tooltip(badge, "Current AI provider and model\nChange it in Settings (⚙️)")

        right = tk.Frame(hdr, bg=self.colors["header_bg"])
        right.pack(side=tk.RIGHT, padx=18, fill=tk.Y)

        theme_icon = "☀️" if self.theme == "dark" else "🌙"
        theme_btn = make_pill_button(
            right, theme_icon, self._toggle_theme,
            bg=self.colors["accent"], fg=self.colors["text_primary"], colors=self.colors,
            hover_bg=self.colors["bg_input"], radius=14, min_width=36, min_height=36)
        theme_btn.pack(side=tk.LEFT, padx=(0, 8), pady=BTN_PAD_Y)
        add_tooltip(theme_btn, "Switch between dark and light theme")

        settings_btn = make_pill_button(
            right, "⚙️", self._open_settings,
            bg=self.colors["accent"], fg=self.colors["text_primary"], colors=self.colors,
            hover_bg=self.colors["bg_input"], radius=14, min_width=36, min_height=36)
        settings_btn.pack(side=tk.LEFT, padx=(0, 14), pady=BTN_PAD_Y)
        add_tooltip(settings_btn, "Settings (Ctrl+,)\nProvider, model, temperature, tool use")

        # No API key UI at all for Ollama — it needs none, and the provider
        # badge on the left already says "Ollama" clearly. Showing a row of
        # disabled controls just to say "not needed" wasted header width for
        # no benefit; only build this when there's actually a key to manage.
        needs_key = provider in ("claude", "deepseek")
        if needs_key:
            tk.Label(right, text=f"{PROVIDER_LABELS[provider]} API Key:",
                     font=("Segoe UI", 9), fg=self.colors["text_dim"],
                     bg=self.colors["header_bg"]).pack(side=tk.LEFT, pady=BTN_PAD_Y)

            entry_cv, entry = make_rounded_entry(
                right, self.colors, self.api_var, show="*", width=28, radius=10)
            entry_cv.pack(side=tk.LEFT, padx=(6, 8), pady=BTN_PAD_Y)
            add_tooltip(entry_cv, f"Paste your {PROVIDER_LABELS[provider]} API key, "
                                 f"then click Set Key")

            set_key_btn = make_pill_button(
                right, "Set Key", self._set_api_key,
                bg=self.colors["accent"], fg=self.colors["text_primary"], colors=self.colors,
                hover_bg=self.colors["bg_input"], radius=10)
            set_key_btn.pack(side=tk.LEFT, pady=BTN_PAD_Y)

    # ── Body (sidebar + tabbed chat area) ─────────
    def _build_body(self):
        body = tk.Frame(self.root, bg=self.colors["bg_dark"])
        body.pack(fill=tk.BOTH, expand=True)

        self._build_sidebar(body)
        self._build_chat_area(body)

    def _build_chat_area(self, parent):
        wrap = tk.Frame(parent, bg=self.colors["bg_dark"])
        wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        toolbar = tk.Frame(wrap, bg=self.colors["bg_panel"])
        toolbar.pack(fill=tk.X, side=tk.TOP)

        new_btn = make_pill_button(
            toolbar, "➕ New Ticket", self.new_session,
            bg=self.colors["accent"], fg=self.colors["text_primary"], colors=self.colors,
            hover_bg=self.colors["bg_input"], radius=10)
        new_btn.pack(side=tk.LEFT, padx=(8, 2), pady=4)
        add_tooltip(new_btn, "Open a new ticket in its own tab  (Ctrl+N)")

        close_btn = make_pill_button(
            toolbar, "✕ Close Tab", self.close_active_session,
            bg=self.colors["bg_panel"], fg=self.colors["text_dim"], colors=self.colors,
            hover_bg=self.colors["bg_input"], radius=10)
        close_btn.pack(side=tk.LEFT, padx=2, pady=4)
        add_tooltip(close_btn, "Close the current tab  (Ctrl+W)")

        tk.Label(toolbar, text="Ctrl+Tab to switch tabs · Ctrl+, for settings",
                 font=("Segoe UI", 8), fg=self.colors["text_ghost"],
                 bg=self.colors["bg_panel"]).pack(side=tk.RIGHT, padx=10)

        self.notebook = ttk.Notebook(wrap)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_sidebar(self, parent):
        sb = tk.Frame(parent, bg=self.colors["bg_panel"], width=220)
        sb.pack(side=tk.LEFT, fill=tk.Y)
        sb.pack_propagate(False)

        def section_label(text):
            tk.Label(sb, text=text, font=("Segoe UI", 8, "bold"),
                     fg=self.colors["text_ghost"], bg=self.colors["bg_panel"]
                     ).pack(anchor="w", padx=18, pady=(16, 2))

        def nav_row(text, command, danger=False):
            fg_normal = self.colors["accent_lit"] if danger else self.colors["text_dim"]
            lbl = tk.Label(sb, text=text, anchor="w", font=("Segoe UI", 10),
                          fg=fg_normal, bg=self.colors["bg_panel"], cursor="hand2",
                          padx=18, pady=5)
            lbl.pack(fill=tk.X)
            lbl.bind("<Enter>", lambda e: lbl.configure(
                bg=self.colors["bg_input"], fg=self.colors["text_primary"]))
            lbl.bind("<Leave>", lambda e: lbl.configure(
                bg=self.colors["bg_panel"], fg=fg_normal))
            lbl.bind("<Button-1>", lambda e: command())
            return lbl

        section_label("QUICK ACTIONS")
        for label, prompt in QUICK_ACTIONS:
            nav_row(label, lambda p=prompt: self.active_session.quick_send(p))

        tk.Frame(sb, bg=self.colors["border"], height=1).pack(
            fill=tk.X, padx=18, pady=(14, 0))

        section_label("ACTIVE TICKET")
        self.msg_count_var = tk.StringVar(value="0 exchanges")
        tk.Label(sb, textvariable=self.msg_count_var,
                 font=("Segoe UI", 8), fg=self.colors["text_ghost"],
                 bg=self.colors["bg_panel"]).pack(anchor="w", padx=18, pady=(0, 4))

        nav_row("📋  Copy Conversation", lambda: self.active_session.copy_conversation())
        nav_row("🎫  Copy Last Ticket", lambda: self.active_session.copy_last_ticket())
        nav_row("💾  Export Chat", lambda: self.active_session.export_chat())
        nav_row("🎟️  View All Tickets", self._open_tickets_viewer)
        nav_row("📚  Knowledge Base", self._open_kb_viewer)
        nav_row("🗑️  Clear This Ticket", lambda: self.active_session.clear(), danger=True)

    # ── API key ───────────────────────────────────
    def _set_api_key(self):
        provider = self.settings["provider"]
        if provider not in ("claude", "deepseek"):
            return
        k = self.api_var.get().strip()
        if k:
            self.api_keys[provider] = k
            config_key = "api_key" if provider == "claude" else "deepseek_api_key"
            self.config[config_key] = k
            save_config(self.config)
            messagebox.showinfo("API Key", f"{PROVIDER_LABELS[provider]} API key saved.")
        else:
            messagebox.showwarning(
                "API Key", f"Please paste a valid {PROVIDER_LABELS[provider]} API key.")

    # ── Theme toggle ───────────────────────────────
    def _toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.colors = THEMES[self.theme]
        self.config["theme"] = self.theme
        save_config(self.config)
        self._rebuild_ui()

    def _rebuild_ui(self):
        active = self.active_session
        active_idx = self.sessions.index(active) if active in self.sessions else 0
        existing_sessions = self.sessions

        for w in self.root.winfo_children():
            w.destroy()
        self.root.configure(bg=self.colors["bg_dark"])
        self._apply_ttk_style()
        self._build_header()
        self._build_body()

        self.sessions = []
        for s in existing_sessions:
            s.frame = None
            frame = s.build_ui(self.notebook)
            self.notebook.add(frame, text=f"  {s.title}  ")
            self.sessions.append(s)
            s.show_initial()

        if self.sessions:
            clamped = max(0, min(active_idx, len(self.sessions) - 1))
            self.notebook.select(self.sessions[clamped].frame)
        self._refresh_session_label()

    # ── Settings panel ─────────────────────────────
    def _open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("440x440")
        win.configure(bg=self.colors["bg_panel"])
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        label_to_provider = {v: k for k, v in PROVIDER_LABELS.items()}

        style = ttk.Style()
        style.configure("Settings.TNotebook", background=self.colors["bg_panel"], borderwidth=0)
        style.configure("Settings.TNotebook.Tab", background=self.colors["bg_input"],
                       foreground=self.colors["text_dim"], padding=(16, 8),
                       font=("Segoe UI", 9))
        style.map("Settings.TNotebook.Tab",
                 background=[("selected", self.colors["accent_lit"])],
                 foreground=[("selected", "white")])

        notebook = ttk.Notebook(win, style="Settings.TNotebook")
        notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 0))

        tab_provider = tk.Frame(notebook, bg=self.colors["bg_panel"])
        tab_generation = tk.Frame(notebook, bg=self.colors["bg_panel"])
        tab_tools = tk.Frame(notebook, bg=self.colors["bg_panel"])
        notebook.add(tab_provider, text="Provider")
        notebook.add(tab_generation, text="Generation")
        notebook.add(tab_tools, text="Tools")

        def field_label(parent, text):
            tk.Label(parent, text=text, font=("Segoe UI", 9, "bold"),
                     fg=self.colors["text_dim"], bg=self.colors["bg_panel"]
                     ).pack(anchor="w", padx=4, pady=(16, 4))

        # ── Provider tab ──
        field_label(tab_provider, "Provider")
        provider_var = tk.StringVar(value=PROVIDER_LABELS[self.settings["provider"]])
        provider_combo = ttk.Combobox(
            tab_provider, textvariable=provider_var,
            values=[PROVIDER_LABELS[p] for p in PROVIDERS],
            state="readonly", font=("Segoe UI", 9))
        provider_combo.pack(fill=tk.X, padx=4)

        field_label(tab_provider, "Model")
        model_var = tk.StringVar()
        model_combo = ttk.Combobox(tab_provider, textvariable=model_var, font=("Segoe UI", 9))
        model_combo.pack(fill=tk.X, padx=4)

        hint_lbl = tk.Label(tab_provider, text="", font=("Segoe UI", 8),
                           fg=self.colors["text_ghost"], bg=self.colors["bg_panel"],
                           wraplength=380, justify=tk.LEFT)
        hint_lbl.pack(anchor="w", padx=4, pady=(6, 0))

        def refresh_model_field(*_):
            provider_key = label_to_provider[provider_var.get()]
            if provider_key == "claude":
                model_combo.configure(values=MODEL_OPTIONS, state="readonly")
                current = self.settings["model"]
                model_var.set(current if current in MODEL_OPTIONS else MODEL_OPTIONS[0])
                hint_lbl.configure(text="Needs an Anthropic API key (set it in the header).")
            elif provider_key == "deepseek":
                model_combo.configure(values=DEEPSEEK_MODEL_OPTIONS, state="readonly")
                current = self.settings["deepseek_model"]
                model_var.set(current if current in DEEPSEEK_MODEL_OPTIONS
                             else DEEPSEEK_MODEL_OPTIONS[0])
                hint_lbl.configure(text="Needs a DeepSeek API key (set it in the header).")
            else:
                ollama_models = fetch_ollama_models()
                if ollama_models:
                    model_combo.configure(values=ollama_models, state="readonly")
                    current = self.settings["ollama_model"]
                    model_var.set(current if current in ollama_models else ollama_models[0])
                    update_ollama_caps_hint()
                else:
                    model_combo.configure(values=[], state="normal")
                    model_var.set(self.settings["ollama_model"])
                    hint_lbl.configure(
                        text="Couldn't reach Ollama at localhost:11434 — type a model name "
                             "manually (e.g. llama3.2) and make sure `ollama serve` is running.")

        def update_ollama_caps_hint(*_):
            if label_to_provider[provider_var.get()] != "ollama":
                return
            caps = get_ollama_model_capabilities(model_var.get())
            base = "Runs locally — free, no API key, no internet needed."
            if caps:
                base += f" Detected support: {', '.join(sorted(caps))}."
            else:
                base += (" No tool-use/vision support detected for this model name — "
                        "those features will be hidden.")
            hint_lbl.configure(text=base)

        refresh_model_field()
        provider_combo.bind("<<ComboboxSelected>>", refresh_model_field)
        model_combo.bind("<<ComboboxSelected>>", update_ollama_caps_hint)

        # ── Generation tab ──
        field_label(tab_generation, "Temperature  (0.0 = focused, 1.0 = creative)")
        temp_var = tk.DoubleVar(value=self.settings["temperature"])
        temp_value_lbl = tk.Label(tab_generation, text=f"{temp_var.get():.1f}",
                                  font=("Segoe UI", 9), fg=self.colors["text_primary"],
                                  bg=self.colors["bg_panel"])
        temp_value_lbl.pack(anchor="e", padx=4)
        tk.Scale(tab_generation, from_=0.0, to=1.0, resolution=0.1, orient=tk.HORIZONTAL,
                 variable=temp_var, bg=self.colors["bg_panel"],
                 fg=self.colors["text_primary"], highlightthickness=0,
                 troughcolor=self.colors["bg_input"],
                 command=lambda v: temp_value_lbl.configure(text=f"{float(v):.1f}")
                 ).pack(fill=tk.X, padx=4)

        field_label(tab_generation, "Max reply length (tokens)")
        tokens_var = tk.IntVar(value=self.settings["max_tokens"])
        tk.Spinbox(tab_generation, from_=256, to=4096, increment=128, textvariable=tokens_var,
                  font=("Segoe UI", 9), bg=self.colors["bg_input"],
                  fg=self.colors["text_primary"], relief=tk.FLAT
                  ).pack(fill=tk.X, padx=4)

        # ── Tools tab ──
        tools_var = tk.BooleanVar(value=self.settings["tools_enabled"])
        tk.Checkbutton(tab_tools, text="Enable tool use — account lookups, password "
                              "reset, ticket status, web search (Claude + capable "
                              "Ollama models)",
                      variable=tools_var, font=("Segoe UI", 9), fg=self.colors["text_dim"],
                      bg=self.colors["bg_panel"], activebackground=self.colors["bg_panel"],
                      selectcolor=self.colors["bg_input"], relief=tk.FLAT,
                      wraplength=380, justify=tk.LEFT, cursor="hand2"
                      ).pack(anchor="w", padx=4, pady=(18, 0))

        diagnostics_var = tk.BooleanVar(value=self.settings["diagnostics_enabled"])
        tk.Checkbutton(tab_tools, text="Allow real local diagnostics (read-only) — lets "
                              "the assistant run ipconfig, ping, systeminfo, etc. on "
                              "THIS computer. No changes are ever made.",
                      variable=diagnostics_var, font=("Segoe UI", 9), fg=self.colors["text_dim"],
                      bg=self.colors["bg_panel"], activebackground=self.colors["bg_panel"],
                      selectcolor=self.colors["bg_input"], relief=tk.FLAT,
                      wraplength=380, justify=tk.LEFT, cursor="hand2"
                      ).pack(anchor="w", padx=4, pady=(14, 0))

        def save_and_close():
            provider_key = label_to_provider[provider_var.get()]
            self.settings["provider"] = provider_key
            if provider_key == "claude":
                self.settings["model"] = model_var.get()
            elif provider_key == "deepseek":
                self.settings["deepseek_model"] = model_var.get().strip() or DEFAULT_DEEPSEEK_MODEL
            else:
                self.settings["ollama_model"] = model_var.get().strip() or DEFAULT_OLLAMA_MODEL
            self.settings["temperature"] = round(temp_var.get(), 1)
            self.settings["max_tokens"] = tokens_var.get()
            self.settings["tools_enabled"] = tools_var.get()
            self.settings["diagnostics_enabled"] = diagnostics_var.get()
            self.config.update(self.settings)
            save_config(self.config)
            self.api_var.set(self.api_keys.get(provider_key, ""))
            win.destroy()
            self._rebuild_ui()

        btn_row = tk.Frame(win, bg=self.colors["bg_panel"])
        btn_row.pack(fill=tk.X, padx=12, pady=12)
        make_pill_button(btn_row, "Save", save_and_close,
                        bg=self.colors["accent_lit"], fg="white", colors=self.colors,
                        hover_bg=self.colors["send_hover"], radius=10
                        ).pack(side=tk.RIGHT)
        make_pill_button(btn_row, "Cancel", win.destroy,
                        bg=self.colors["bg_input"], fg=self.colors["text_dim"], colors=self.colors,
                        hover_bg=self.colors["border"], radius=10
                        ).pack(side=tk.RIGHT, padx=(0, 8))

    # ── Ticket viewer ──────────────────────────────
    def _open_tickets_viewer(self):
        win = tk.Toplevel(self.root)
        win.title("Tickets")
        win.geometry("620x360")
        win.configure(bg=self.colors["bg_panel"])
        win.transient(self.root)

        cols = ("ticket", "created", "summary", "status")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=12)
        tree.heading("ticket", text="Ticket")
        tree.heading("created", text="Created")
        tree.heading("summary", text="Issue")
        tree.heading("status", text="Status")
        tree.column("ticket", width=90)
        tree.column("created", width=130)
        tree.column("summary", width=280)
        tree.column("status", width=80)
        tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        def reload_rows():
            tree.delete(*tree.get_children())
            for ticket_number, created_at, summary, status in fetch_tickets():
                tree.insert("", tk.END,
                           values=(ticket_number, created_at, (summary or "")[:60], status))

        def toggle_status(_event):
            sel = tree.selection()
            if not sel:
                return
            ticket_number, _, _, status = tree.item(sel[0], "values")
            new_status = "resolved" if status == "open" else "open"
            set_ticket_status(ticket_number, new_status)
            reload_rows()

        tree.bind("<Double-1>", toggle_status)
        tk.Label(win, text="Double-click a row to toggle open/resolved",
                 font=("Segoe UI", 8), fg=self.colors["text_dim"],
                 bg=self.colors["bg_panel"]).pack(anchor="w", padx=12, pady=(0, 10))

        reload_rows()

    # ── Knowledge base viewer / editor ─────────────
    def _open_kb_viewer(self):
        win = tk.Toplevel(self.root)
        win.title("Internal Knowledge Base")
        win.geometry("680x560")
        win.configure(bg=self.colors["bg_panel"])
        win.transient(self.root)

        tk.Label(win, text=f"{len(self.kb)} articles - the assistant automatically "
                          f"references relevant ones based on what's asked",
                 font=("Segoe UI", 8), fg=self.colors["text_dim"],
                 bg=self.colors["bg_panel"]).pack(anchor="w", padx=12, pady=(10, 4))

        cols = ("title", "tags")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=8)
        tree.heading("title", text="Title")
        tree.heading("tags", text="Tags")
        tree.column("title", width=260)
        tree.column("tags", width=380)
        tree.pack(fill=tk.X, padx=12)

        content_wrap = tk.Frame(win, bg=self.colors["bg_input"])
        content_wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 12))

        content_box = tk.Text(content_wrap, height=8, wrap=tk.WORD, font=("Segoe UI", 9),
                              bg=self.colors["bg_input"], fg=self.colors["text_primary"],
                              relief=tk.FLAT, padx=8, pady=8)
        content_scroll = ttk.Scrollbar(content_wrap, orient="vertical",
                                       command=content_box.yview, style="Vertical.TScrollbar")
        content_box.configure(state=tk.DISABLED, yscrollcommand=content_scroll.set)
        content_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        content_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def reload_rows():
            tree.delete(*tree.get_children())
            for entry in self.kb:
                tree.insert("", tk.END,
                           values=(entry["title"], ", ".join(entry.get("tags", []))))

        def show_selected(_event):
            sel = tree.selection()
            if not sel:
                return
            idx = tree.index(sel[0])
            content_box.configure(state=tk.NORMAL)
            content_box.delete("1.0", tk.END)
            content_box.insert("1.0", self.kb[idx]["content"])
            content_box.configure(state=tk.DISABLED)

        tree.bind("<<TreeviewSelect>>", show_selected)
        reload_rows()

        tk.Frame(win, bg=self.colors["border"], height=1).pack(fill=tk.X, padx=12, pady=(0, 10))
        tk.Label(win, text="Add a new article", font=("Segoe UI", 9, "bold"),
                 fg=self.colors["text_dim"], bg=self.colors["bg_panel"]
                 ).pack(anchor="w", padx=12)

        form = tk.Frame(win, bg=self.colors["bg_panel"])
        form.pack(fill=tk.X, padx=12, pady=(6, 12))

        title_var = tk.StringVar()
        tags_var = tk.StringVar()
        title_cv, _ = make_rounded_entry(form, self.colors, title_var, width=40, radius=8)
        title_cv.pack(fill=tk.X, pady=(0, 4))
        tk.Label(form, text="Title", font=("Segoe UI", 7), fg=self.colors["text_ghost"],
                 bg=self.colors["bg_panel"]).pack(anchor="w")

        tags_cv, _ = make_rounded_entry(form, self.colors, tags_var, width=40, radius=8)
        tags_cv.pack(fill=tk.X, pady=(8, 4))
        tk.Label(form, text="Tags (comma-separated)", font=("Segoe UI", 7),
                 fg=self.colors["text_ghost"], bg=self.colors["bg_panel"]).pack(anchor="w")

        new_content_box = tk.Text(form, height=4, wrap=tk.WORD, font=("Segoe UI", 9),
                                  bg=self.colors["bg_input"], fg=self.colors["text_primary"],
                                  relief=tk.FLAT, padx=6, pady=6,
                                  insertbackground=self.colors["text_primary"])
        new_content_box.pack(fill=tk.X, pady=(8, 0))
        tk.Label(form, text="Content / procedure", font=("Segoe UI", 7),
                 fg=self.colors["text_ghost"], bg=self.colors["bg_panel"]).pack(anchor="w")

        def add_entry():
            title = title_var.get().strip()
            content = new_content_box.get("1.0", "end-1c").strip()
            if not title or not content:
                messagebox.showwarning("Add Article", "Title and content are required.")
                return
            tags = [t.strip() for t in tags_var.get().split(",") if t.strip()]
            self.kb.append({"title": title, "tags": tags, "content": content})
            save_knowledge_base(self.kb)
            reload_rows()
            title_var.set("")
            tags_var.set("")
            new_content_box.delete("1.0", tk.END)
            active = self.active_session
            if active is not None:
                active.status_var.set("Article added to knowledge base")
                self.root.after(3000, lambda: active.status_var.set(""))

        make_pill_button(win, "Save Article", add_entry,
                        bg=self.colors["accent_lit"], fg="white", colors=self.colors,
                        hover_bg=self.colors["send_hover"], radius=10
                        ).pack(anchor="e", padx=12, pady=(0, 12))
# ────────────────────────────────────────────────
#  Entry point
# ────────────────────────────────────────────────
def main():
    init_tickets_db()
    root = tk.Tk()
    ITSupportApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
