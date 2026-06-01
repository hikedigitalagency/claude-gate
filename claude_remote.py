#!/usr/bin/env python3
"""
Claude Remote Approval System
==============================
Ek hi file mein sab kuch — terminal + phone (Telegram) se Claude ko approve/deny karo.

Usage:
  python3 ~/.claude/claude_remote.py setup    — Pehli baar: bot token + chat_id set karo
  python3 ~/.claude/claude_remote.py install  — LaunchAgent + Claude hook register karo
  python3 ~/.claude/claude_remote.py broker   — Broker daemon chalao (install ke baad auto hota hai)
  python3 ~/.claude/claude_remote.py hook     — Claude Code hook (settings.json se automatically call hota hai)
  python3 ~/.claude/claude_remote.py status   — Active sessions + pending requests dekho
  python3 ~/.claude/claude_remote.py clear    — Sab Yes-All flags hatao
"""

import sys, os, json, time, select, subprocess, uuid, signal, logging, argparse
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

CONFIG_FILE   = Path.home() / ".claude" / "remote_config.json"
BROKER_DIR    = Path("/tmp/claude-broker")
REQ_DIR       = BROKER_DIR / "requests"
RESP_DIR      = BROKER_DIR / "responses"
SESSION_DIR   = BROKER_DIR / "sessions"
LOG_FILE      = BROKER_DIR / "broker.log"
PLIST_PATH    = Path.home() / "Library/LaunchAgents/com.claude.broker.plist"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

TTY_TIMEOUT   = 10       # seconds — laptop pe terminal prompt wait
TG_TIMEOUT    = 300      # seconds — Telegram response wait (5 min)
SESSION_TTL   = 8 * 3600 # seconds — Yes-All flag expiry (8 hours)

# Tools jinhe silently approve karo (read-only, safe)
SAFE_TOOLS = {"Read", "Glob", "Grep", "LS"}

# Bash commands jinhe silently approve karo
SAFE_BASH_PREFIXES = (
    "git status", "git log", "git diff", "git show",
    "ls", "cat ", "echo ", "pwd", "which ", "env",
    "find ", "grep ", "head ", "tail ", "wc ",
    "python3 -c", "node -e",
    # broker diagnostics
    "python3 ~/.claude/claude_remote.py",
    "pgrep", "launchctl",
)


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}

def save_config(cfg):
    CONFIG_FILE.parent.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Project Identity — auto-detect karo
# ─────────────────────────────────────────────────────────────────────────────

def get_project_identity():
    """
    Priority:
    1. CLAUDE.md mein "Project: XYZ" line
    2. Git repo name + current branch
    3. Current folder name
    """
    # 1. CLAUDE.md
    claude_md = Path.cwd() / "CLAUDE.md"
    if claude_md.exists():
        for line in claude_md.read_text().splitlines():
            if line.strip().lower().startswith("project:"):
                name = line.split(":", 1)[1].strip()
                if name:
                    return name

    # 2. Git
    try:
        repo = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL, text=True
        ).strip().rstrip("/").split("/")[-1].replace(".git", "")

        branch = subprocess.check_output(
            ["git", "branch", "--show-current"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()

        if repo:
            return f"{repo}/{branch}" if branch else repo
    except Exception:
        pass

    # 3. Folder name
    return Path.cwd().name


# ─────────────────────────────────────────────────────────────────────────────
# Session / Yes-All Management
# ─────────────────────────────────────────────────────────────────────────────

def get_session_id():
    """Unique ID per Claude process: project-slug + pid"""
    slug = get_project_identity().replace("/", "-").replace(" ", "-")[:40]
    return f"{slug}-{os.getpid()}"

def is_yes_all(session_id: str) -> bool:
    flag = SESSION_DIR / f"{session_id}.flag"
    if not flag.exists():
        return False
    if time.time() - flag.stat().st_mtime > SESSION_TTL:
        flag.unlink(missing_ok=True)
        return False
    return True

def set_yes_all(session_id: str):
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    (SESSION_DIR / f"{session_id}.flag").write_text("1")

def clear_all_flags():
    if SESSION_DIR.exists():
        for f in SESSION_DIR.glob("*.flag"):
            f.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Terminal Prompt — laptop pe seedha puchho
# ─────────────────────────────────────────────────────────────────────────────

def ask_terminal(project: str, tool: str, detail: str):
    """
    macOS native dialog — Claude Code ke saath best compatible.
    /dev/tty hook subprocess mein kaam nahi karta (TUI limitation).
    Returns: 'yes' | 'all' | 'no' | ('instruct', text) | None
    """
    try:
        safe_msg = (project + chr(10) + 'Tool: ' + detail[:120]).replace('"', "'")
        script = (
            'tell application "System Events" to set frontApp to name of first process whose frontmost is true' + chr(10) +
            'with timeout ' + str(TTY_TIMEOUT) + ' seconds' + chr(10) +
            '  tell application frontApp to activate' + chr(10) +
            '  set result to display dialog "' + safe_msg + '" ' +
            'with title "Claude Approval (' + str(TTY_TIMEOUT) + 's)" ' +
            'default answer "" ' +
            'buttons {"No", "Yes All", "Yes"} ' +
            'default button "Yes")' + chr(10) +
            '  set btn to button returned of result' + chr(10) +
            '  set txt to text returned of result' + chr(10) +
            'end timeout' + chr(10) +
            'return btn & "|" & txt'
        )
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=TTY_TIMEOUT + 3
        )
        out  = r.stdout.strip()
        parts = out.split("|", 1)
        btn  = parts[0].strip()
        txt  = parts[1].strip() if len(parts) > 1 else ""

        if btn == "No":      return "no"
        if btn == "Yes All": return "all"
        if btn == "Yes":
            if txt:           return ("instruct", txt)
            return "yes"
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────────────────────

class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self.base    = f"https://api.telegram.org/bot{token}"
        self._last_update_id = None

    def _post(self, endpoint: str, payload: dict):
        import urllib.request
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{self.base}/{endpoint}", data=data,
            headers={"Content-Type": "application/json"}
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def _get(self, endpoint: str, params: str = ""):
        import urllib.request
        url = f"{self.base}/{endpoint}?{params}"
        return json.loads(urllib.request.urlopen(url, timeout=15).read())

    def send_approval_request(self, req_id: str, project: str, tool: str, detail: str):
        """Phone pe yes/no/all buttons ke saath message bhejo"""
        emoji = {"Bash": "🔴", "Write": "🟠", "Edit": "🟡"}.get(tool, "🔵")
        text  = (
            f"{emoji} *{project}*\n"
            f"Tool: `{tool}`\n"
            f"```\n{detail[:400]}\n```"
        )
        r = self._post("sendMessage", {
            "chat_id":    self.chat_id,
            "text":       text,
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [
                [
                    {"text": "✅ Yes",              "callback_data": f"yes:{req_id}"},
                    {"text": "✅ Yes All (session)", "callback_data": f"all:{req_id}"},
                    {"text": "❌ No",               "callback_data": f"no:{req_id}"},
                ],
                [
                    {"text": "✏️ Instruct", "callback_data": f"instruct:{req_id}"},
                ],
            ]}
        })
        return r.get("result", {}).get("message_id", 0)

    def send_text(self, text: str):
        self._post("sendMessage", {
            "chat_id": self.chat_id,
            "text":    text,
        })

    def skip_old_updates(self):
        """Broker start pe purane updates skip karo"""
        try:
            data = self._get("getUpdates", "limit=1&offset=-1")
            for u in data.get("result", []):
                self._last_update_id = u["update_id"]
        except Exception:
            pass

    def send_instruction_prompt(self, req_id: str, orig_msg_id: int) -> int:
        """Instruct button tap ke baad instruction maango"""
        # Edit original message buttons to show waiting state
        try:
            self._post("editMessageReplyMarkup", {
                "chat_id": self.chat_id,
                "message_id": orig_msg_id,
                "reply_markup": {"inline_keyboard": [[
                    {"text": "⏳ Waiting for instruction...", "callback_data": "done"}
                ]]}
            })
        except Exception:
            pass
        # Send force_reply message
        r = self._post("sendMessage", {
            "chat_id": self.chat_id,
            "text": f"✏️ *Type your instruction for Claude:*\n_(Reply to cancel: send `-`)_",
            "parse_mode": "Markdown",
            "reply_markup": {"force_reply": True, "selective": False}
        })
        return r.get("result", {}).get("message_id", 0)

    def poll_messages(self) -> list:
        """
        Naye text message updates fetch karo (non-callback).
        Returns list of message text strings.
        """
        params = "timeout=0"
        if self._last_update_id:
            params += f"&offset={self._last_update_id + 1}"

        try:
            data = self._get("getUpdates", params)
        except Exception as e:
            logging.error(f"poll_messages getUpdates error: {e}")
            return []

        results = []
        for u in data.get("result", []):
            uid = u["update_id"]
            self._last_update_id = uid
            msg = u.get("message")
            if not msg:
                continue
            text = msg.get("text", "")
            if text and not text.startswith("/"):
                results.append(text)
        return results

    def poll_callbacks(self) -> list:
        """
        Naye callback_query updates fetch karo.
        Returns list of (req_id, decision)
        """
        params = "timeout=10"
        if self._last_update_id:
            params += f"&offset={self._last_update_id + 1}"

        try:
            data = self._get("getUpdates", params)
        except Exception as poll_err:
            logging.error(f"poll_callbacks getUpdates error: {poll_err}")
            return []

        results = []
        updates = data.get("result", [])
        if updates:
            logging.info(f"poll got {len(updates)} updates")
        for u in updates:
            uid = u["update_id"]
            self._last_update_id = uid
            cb = u.get("callback_query")
            if not cb:
                msg_text = u.get("message", {}).get("text", "").strip()
                if msg_text and not msg_text.startswith("/"):
                    logging.info(f"  update {uid}: message text={msg_text[:40]}")
                    results.append(("__msg__", msg_text, 0))
                else:
                    logging.info(f"  update {uid}: message (skip)")
                continue
            cb_data = cb["data"]
            logging.info(f"  update {uid}: CALLBACK data={cb_data}")
            # done button tap → sirf dismiss karo
            if cb_data == "done":
                try:
                    self._post("answerCallbackQuery", {"callback_query_id": cb["id"], "text": ""})
                except Exception:
                    pass
                continue
            parts = cb_data.split(":", 1)
            if len(parts) == 2:
                decision, req_id = parts
                if decision == "instruct":
                    # Answer with prompt hint
                    try:
                        self._post("answerCallbackQuery", {
                            "callback_query_id": cb["id"],
                            "text": "Type your instruction ✏️",
                            "show_alert": False,
                        })
                    except Exception:
                        pass
                    results.append((req_id, "instruct", cb.get("message", {}).get("message_id", 0)))
                    continue
                # Toast popup + message edit karo (clear visual feedback)
                try:
                    label = "Approved ✅" if decision != "no" else "Denied ❌"
                    self._post("answerCallbackQuery", {
                        "callback_query_id": cb["id"],
                        "text": label,
                        "show_alert": False,
                    })
                except Exception:
                    pass
                results.append((req_id, decision, cb.get("message", {}).get("message_id", 0)))
        return results


# ─────────────────────────────────────────────────────────────────────────────
# Broker Daemon — central manager
# ─────────────────────────────────────────────────────────────────────────────

PENDING_FILE = BROKER_DIR / "pending.json"

def save_pending(pending: dict):
    try:
        BROKER_DIR.mkdir(parents=True, exist_ok=True)
        PENDING_FILE.write_text(json.dumps(pending, indent=2))
    except Exception:
        pass

def load_pending() -> dict:
    if PENDING_FILE.exists():
        try:
            data = json.loads(PENDING_FILE.read_text())
            now = time.time()
            return {k: v for k, v in data.items() if now - v.get("sent_at", 0) < TG_TIMEOUT}
        except Exception:
            pass
    return {}


def run_broker():
    cfg = load_config()
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        print("❌ Pehle setup karo: python3 ~/.claude/claude_remote.py setup")
        sys.exit(1)

    for d in (REQ_DIR, RESP_DIR, SESSION_DIR):
        d.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.info("── Broker started ──")
    print(f"✓ Claude Remote Broker running  (log: {LOG_FILE})\n  Ctrl+C to stop")

    # Zombie broker processes kill karo (apne aap ko chodd ke)
    import signal as _sig
    mypid = os.getpid()
    try:
        r = subprocess.run(["pgrep", "-f", "claude_remote.py broker"],
                           capture_output=True, text=True)
        for pid_str in r.stdout.strip().splitlines():
            try:
                pid = int(pid_str.strip())
                if pid != mypid:
                    os.kill(pid, _sig.SIGTERM)
                    time.sleep(0.3)
            except Exception:
                pass
    except Exception:
        pass

    bot = TelegramBot(cfg["bot_token"], cfg["chat_id"])
    bot.skip_old_updates()

    # Disk se pending recover karo (restart ke baad bhi kaam kare)
    pending: dict = load_pending()
    if pending:
        logging.info(f"Recovered {len(pending)} pending requests")

    # req_id → {**info, "prompt_msg_id": int, "sent_at": float}
    waiting_instruction: dict = {}

    def handle_decision(req_id: str, decision: str, session_id: str, source: str):
        resp_file = RESP_DIR / f"resp-{req_id}.json"
        if decision == "all":
            set_yes_all(session_id)
            resp_file.write_text(json.dumps({"decision": "approve"}))
        elif decision == "yes":
            resp_file.write_text(json.dumps({"decision": "approve"}))
        else:
            resp_file.write_text(json.dumps({
                "decision": "block",
                "reason":   f"Denied via {source}",
            }))
        logging.info(f"[{source}] {decision.upper():4s}  {req_id}")

    while True:
        try:
            # ── 1. New request files ─────────────────────────────────────────
            for req_file in sorted(REQ_DIR.glob("req-*.json")):
                req_id = req_file.stem[4:]  # strip "req-"
                if req_id in pending:
                    continue
                if (RESP_DIR / f"resp-{req_id}.json").exists():
                    req_file.unlink(missing_ok=True)
                    continue

                try:
                    data = json.loads(req_file.read_text())
                except Exception:
                    continue

                session_id = data.get("session_id", "")

                # Yes-All active → silent approve
                if is_yes_all(session_id):
                    handle_decision(req_id, "yes", session_id, "yes-all-flag")
                    req_file.unlink(missing_ok=True)
                    continue

                # Terminal prompt ab hook mein handle hota hai — seedha Telegram
                try:
                    msg_id = bot.send_approval_request(
                        req_id,
                        data.get("project", "unknown"),
                        data.get("tool",    "unknown"),
                        data.get("detail",  ""),
                    )
                    pending[req_id] = {**data, "sent_at": time.time(), "msg_id": msg_id}
                    save_pending(pending)
                    logging.info(f"[telegram] SENT  {req_id}  [{data.get('project')}]")
                except Exception as e:
                    # Telegram fail → fallback block
                    logging.error(f"Telegram send failed: {e}")
                    handle_decision(req_id, "no", session_id, "telegram-error")

                req_file.unlink(missing_ok=True)

            # ── 2. Telegram callbacks ────────────────────────────────────────
            if pending or waiting_instruction:
                for req_id, decision, msg_id in bot.poll_callbacks():
                    # Text message reply (instruction)
                    if req_id == "__msg__":
                        text = decision
                        if waiting_instruction:
                            wi_req_id = next(iter(waiting_instruction))
                            info = waiting_instruction.pop(wi_req_id)
                            resp_file = RESP_DIR / f"resp-{wi_req_id}.json"
                            if text.strip() == "-":
                                resp_file.write_text(json.dumps({"decision": "block", "reason": "User cancelled"}))
                                logging.info(f"[instruction] CANCELLED  {wi_req_id}")
                            else:
                                resp_file.write_text(json.dumps({"decision": "block", "reason": text}))
                                logging.info(f"[instruction] SENT  {wi_req_id}  text={text[:40]}")
                            try:
                                bot.send_text("✅ Claude ko instruction di gayi: " + text)
                            except Exception:
                                pass
                        continue
                    if decision == "instruct":
                        if req_id not in pending:
                            continue
                        info = pending.pop(req_id)
                        save_pending(pending)
                        prompt_msg_id = bot.send_instruction_prompt(req_id, msg_id or info.get("msg_id", 0))
                        waiting_instruction[req_id] = {**info, "prompt_msg_id": prompt_msg_id, "sent_at": time.time()}
                        logging.info(f"[telegram] INSTRUCT  {req_id}  waiting for text reply")
                        continue
                    if req_id not in pending:
                        continue
                    info = pending.pop(req_id)
                    save_pending(pending)
                    handle_decision(req_id, decision, info["session_id"], "telegram")
                    # Buttons ko result button se replace karo (unclickable jaisa)
                    try:
                        icon   = "✅" if decision in ("yes", "all") else "❌"
                        action = {"yes": "Approved", "all": "Approved (All)", "no": "Denied"}[decision]
                        bot._post("editMessageReplyMarkup", {
                            "chat_id":    bot.chat_id,
                            "message_id": msg_id or info.get("msg_id", 0),
                            "reply_markup": {"inline_keyboard": [[
                                {"text": f"{icon} {action}", "callback_data": "done"}
                            ]]},
                        })
                    except Exception:
                        pass

            # ── 2.5 Instruction text replies ─────────────────────────────────
            if waiting_instruction:
                # Check timeouts first
                for req_id in list(waiting_instruction):
                    info = waiting_instruction[req_id]
                    if time.time() - info.get("sent_at", 0) > TG_TIMEOUT:
                        waiting_instruction.pop(req_id)
                        handle_decision(req_id, "no", info["session_id"], "instruction-timeout")
                        logging.info(f"[instruction-timeout]  {req_id}")
                        continue

                # Poll for text message replies
                text_msgs = bot.poll_messages()
                for text in text_msgs:
                    if not waiting_instruction:
                        break
                    # FIFO: take first pending req_id
                    req_id = next(iter(waiting_instruction))
                    info = waiting_instruction.pop(req_id)
                    resp_file = RESP_DIR / f"resp-{req_id}.json"
                    if text.strip() == "-":
                        # Cancel → block with no reason
                        resp_file.write_text(json.dumps({"decision": "block", "reason": "Instruction cancelled"}))
                        logging.info(f"[instruction-cancel]  {req_id}")
                        try:
                            bot._post("sendMessage", {
                                "chat_id": bot.chat_id,
                                "text": "❌ Instruction cancelled.",
                            })
                        except Exception:
                            pass
                    else:
                        instruction_text = text.strip()
                        resp_file.write_text(json.dumps({"decision": "block", "reason": f"User instruction: {instruction_text}"}))
                        logging.info(f"[instruction]  {req_id}  text={instruction_text[:60]}")
                        try:
                            bot._post("sendMessage", {
                                "chat_id": bot.chat_id,
                                "text": f"✅ Instruction sent: {instruction_text}",
                            })
                        except Exception:
                            pass

            # ── 3. Timeouts ──────────────────────────────────────────────────
            now = time.time()
            for req_id in list(pending):
                if now - pending[req_id]["sent_at"] > TG_TIMEOUT:
                    info = pending.pop(req_id)
                    handle_decision(req_id, "no", info["session_id"], "timeout")

            time.sleep(0.8)

        except KeyboardInterrupt:
            print("\n✓ Broker stopped")
            break
        except Exception as e:
            logging.error(f"Broker loop error: {e}")
            time.sleep(2)


# ─────────────────────────────────────────────────────────────────────────────
# Hook — Claude Code se call hota hai (PreToolUse)
# ─────────────────────────────────────────────────────────────────────────────

def run_hook():
    try:
        hook_data = json.load(sys.stdin)
    except Exception:
        # stdin parse nahi hua → approve (Claude ka normal flow continue karo)
        print(json.dumps({"decision": "approve"}))
        return

    tool_name  = hook_data.get("tool_name", "")
    tool_input = hook_data.get("tool_input", {})

    # Safe tools → silently approve
    if tool_name in SAFE_TOOLS:
        print(json.dumps({"decision": "approve"}))
        return

    # Safe Bash commands → silently approve
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if any(cmd.startswith(p) for p in SAFE_BASH_PREFIXES):
            print(json.dumps({"decision": "approve"}))
            return

    # Detail string banao
    if tool_name == "Bash":
        detail = tool_input.get("command", "")
    elif tool_name in ("Write", "Edit"):
        detail = f"{tool_name}: {tool_input.get('file_path', '?')}"
    elif tool_name == "Agent":
        detail = str(tool_input.get("prompt", ""))[:200]
    else:
        detail = json.dumps(tool_input)[:200]

    session_id = get_session_id()

    # Yes-All flag active? → approve
    if is_yes_all(session_id):
        print(json.dumps({"decision": "approve"}))
        return

    # Laptop pe ho? Terminal pe seedha puchho (10s timeout)
    ans = ask_terminal(get_project_identity(), tool_name, detail)
    if isinstance(ans, tuple) and ans[0] == "instruct":
        print(json.dumps({"decision": "block", "reason": ans[1]}))
        return
    if ans == "all":
        set_yes_all(session_id)
        print(json.dumps({"decision": "approve"}))
        return
    elif ans == "yes":
        print(json.dumps({"decision": "approve"}))
        return
    elif ans == "no":
        print(json.dumps({"decision": "block", "reason": "Denied via terminal"}))
        return

    # Terminal timeout / bahar ho → broker ke zariye Telegram
    for d in (REQ_DIR, RESP_DIR, SESSION_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # Request file likho
    req_id   = uuid.uuid4().hex[:8]
    req_file = REQ_DIR / f"req-{req_id}.json"
    req_file.write_text(json.dumps({
        "id":         req_id,
        "project":    get_project_identity(),
        "session_id": session_id,
        "tool":       tool_name,
        "detail":     detail,
    }))

    # Response ka wait karo
    resp_file = RESP_DIR / f"resp-{req_id}.json"
    deadline  = time.time() + TG_TIMEOUT + TTY_TIMEOUT + 60

    while time.time() < deadline:
        if resp_file.exists():
            try:
                result = json.loads(resp_file.read_text())
                resp_file.unlink(missing_ok=True)
                print(json.dumps(result))
                return
            except Exception:
                pass
        time.sleep(0.5)

    # Deadline cross ho gaya
    req_file.unlink(missing_ok=True)
    print(json.dumps({"decision": "block", "reason": "No response — timed out"}))


# ─────────────────────────────────────────────────────────────────────────────
# Setup — pehli baar config karo
# ─────────────────────────────────────────────────────────────────────────────

def run_setup():
    print("═" * 50)
    print("  Claude Remote Approval — Setup")
    print("═" * 50)
    print()
    print("Telegram bot banana hai? @BotFather se /newbot karo")
    print("Chat ID chahiye? @userinfobot ko message karo")
    print()

    token   = input("Bot Token  : ").strip()
    chat_id = input("Chat ID    : ").strip()

    if not token or not chat_id:
        print("❌ Token aur Chat ID dono chahiye.")
        sys.exit(1)

    save_config({"bot_token": token, "chat_id": chat_id})
    print(f"\n✓ Config save ho gaya: {CONFIG_FILE}")

    # Test message bhejo
    ans = input("\nTest message bhejein Telegram pe? [y/n]: ").strip().lower()
    if ans == "y":
        try:
            bot = TelegramBot(token, chat_id)
            bot.send_text("✅ Claude Remote Approval connected!\nAb aap phone se approve/deny kar sakte ho.")
            print("✓ Test message bhej diya!")
        except Exception as e:
            print(f"❌ Telegram error: {e}\nToken ya Chat ID check karo.")


# ─────────────────────────────────────────────────────────────────────────────
# Install — LaunchAgent + Claude hook
# ─────────────────────────────────────────────────────────────────────────────

def run_install():
    script = os.path.abspath(__file__)
    python = sys.executable

    print("═" * 50)
    print("  Claude Remote Approval — Install")
    print("═" * 50)

    # ── 1. LaunchAgent plist ─────────────────────────────────────────────────
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude.broker</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
        <string>broker</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key>
    <string>/tmp/claude-broker/broker.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-broker/broker.log</string>
    <key>WorkingDirectory</key>
    <string>/tmp</string>
</dict>
</plist>"""

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist)
    print(f"\n✓ LaunchAgent: {PLIST_PATH}")

    # Load/reload launchd
    os.system(f"launchctl unload {PLIST_PATH} 2>/dev/null; launchctl load {PLIST_PATH}")
    print("✓ Broker daemon loaded (Mac boot pe auto-start hoga)")

    # ── 2. Claude settings.json hook ─────────────────────────────────────────
    settings = {}
    if SETTINGS_PATH.exists():
        try:
            settings = json.loads(SETTINGS_PATH.read_text())
        except Exception:
            pass

    hook_cmd  = f"{python} {script} hook"
    new_entry = {
        "matcher": ".*",
        "hooks": [{"type": "command", "command": hook_cmd}]
    }

    hooks = settings.setdefault("hooks", {})
    pre   = hooks.setdefault("PreToolUse", [])

    # Duplicate check
    already = any(
        h.get("hooks", [{}])[0].get("command") == hook_cmd
        for h in pre if isinstance(h, dict)
    )

    if not already:
        pre.append(new_entry)
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
        print(f"✓ PreToolUse hook added: {SETTINGS_PATH}")
    else:
        print(f"✓ Hook already registered: {SETTINGS_PATH}")

    print("\n✓ Install complete!")
    print("  → Claude Code restart karo taaki hook activate ho.")
    print("  → Status check: python3 ~/.claude/claude_remote.py status")


# ─────────────────────────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────────────────────────

def run_status():
    print("═" * 50)
    print("  Claude Remote Approval — Status")
    print("═" * 50)

    # Broker process
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"{os.path.basename(__file__)} broker"],
            capture_output=True, text=True
        )
        pid = result.stdout.strip()
        if pid:
            print(f"\nBroker        : ✅ Running (pid {pid})")
        else:
            print("\nBroker        : ❌ Not running")
            print("               Run: python3 ~/.claude/claude_remote.py broker")
    except Exception:
        pass

    # Config
    cfg = load_config()
    if cfg.get("bot_token"):
        masked = cfg["bot_token"][:6] + "..." + cfg["bot_token"][-4:]
        print(f"Config        : ✅ Token {masked}, Chat {cfg.get('chat_id')}")
    else:
        print("Config        : ❌ Not configured (run setup)")

    # Active Yes-All sessions
    flags = list(SESSION_DIR.glob("*.flag")) if SESSION_DIR.exists() else []
    print(f"\nYes-All flags : {len(flags)}")
    for f in flags:
        age = int((time.time() - f.stat().st_mtime) / 60)
        print(f"  • {f.stem}  ({age}m ago)")

    # Pending requests
    pending = list(REQ_DIR.glob("req-*.json")) if REQ_DIR.exists() else []
    print(f"\nPending reqs  : {len(pending)}")
    for p in pending:
        try:
            d = json.loads(p.read_text())
            print(f"  • [{d.get('project','?')}] {d.get('tool','?')}: {d.get('detail','')[:50]}")
        except Exception:
            pass

    print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Claude Remote Approval System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "mode",
        choices=["setup", "install", "broker", "hook", "status", "clear"],
    )
    args = parser.parse_args()

    if   args.mode == "setup":   run_setup()
    elif args.mode == "install": run_install()
    elif args.mode == "broker":  run_broker()
    elif args.mode == "hook":    run_hook()
    elif args.mode == "status":  run_status()
    elif args.mode == "clear":
        clear_all_flags()
        print("✓ Sab Yes-All flags clear ho gaye")


if __name__ == "__main__":
    main()
