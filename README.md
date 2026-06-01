# Claude Gate

Claude Code ke tool use ko remotely approve/deny karo — phone (Telegram) ya laptop (macOS dialog) se, kahin bhi raho.

---

## Features

- **Telegram notifications** — Yes / Yes All / No / Instruct buttons
- **macOS dialog popup** — laptop pe seedha screen pe approve karo (10s)
- **Custom instruction** — Claude ko redirect karo ("yeh mat karo, woh karo")
- **Multiple Claude sessions** — har session ka project naam auto-detect
- **Yes All (session)** — ek baar approve, poore session ke liye
- **Auto-start on boot** — LaunchAgent se broker hamesha chalta hai
- **Pending state recovery** — broker restart ke baad bhi recover

---

## Setup

### Step 1 — Telegram Bot banao
1. `@BotFather` → `/newbot` → naam do → **token** milega
2. `@userinfobot` → **Chat ID** lo
3. Bot ko `/start` bhejo

### Step 2 — Config
```bash
python3 ~/.claude/claude_remote.py setup
```

### Step 3 — Install
```bash
python3 ~/.claude/claude_remote.py install
```

### Step 4 — Claude Code restart karo

---

## Commands

| Command | Kaam |
|---------|------|
| `cstatus` | Broker status, pending requests, Yes-All flags |
| `cclear` | Saare Yes-All flags hatao |
| `cbroker` | Broker manually start karo |

**Aliases (~/.zshrc mein add karo):**
```bash
alias cstatus='python3 ~/.claude/claude_remote.py status'
alias cclear='python3 ~/.claude/claude_remote.py clear'
alias cbroker='python3 ~/.claude/claude_remote.py broker'
```

---

## Approval Buttons

| Button | Matlab |
|--------|--------|
| Yes | Sirf yeh command approve |
| Yes All (session) | Is session ke sab auto-approve (8 ghante) |
| No | Block — Claude ruk jaata hai |
| Instruct | Block + instruction → Claude naya approach leta hai |

### Instruct Flow
1. **Instruct** tap karo
2. Bot ek message bhejega
3. Us message pe **reply** karo apni instruction se
4. Claude ko milta hai: `{"decision": "block", "reason": "aapki instruction"}`
5. Claude instruction padh ke retry karta hai

**osascript dialog mein bhi:** text field khali → Yes, kuch likho → Instruct

---

## Behavior

| Situation | Kya hoga |
|-----------|----------|
| Laptop pe ho | macOS dialog popup (10s) |
| 10s mein click nahi | Telegram notification |
| Bahar ho (phone) | Telegram se approve/deny |
| Yes All tap | Session ke sab auto-approve (8h) |
| 5 min koi response nahi | Auto-deny |
| Multiple Claude sessions | Alag notifications, alag session flags |

---

## Safe Commands (Auto-approve, No Prompt)

```
git status, git log, git diff, git show
ls, cat, echo, pwd, which, env
find, grep, head, tail, wc
python3 -c, node -e
```

Tools: `Read`, `Glob`, `Grep`, `LS` — hamesha approve

---

## Architecture

```
Claude Code (PreToolUse hook)
        |
        v
Hook Script (claude_remote.py hook)
  |-- Safe command? --> Auto-approve
  |-- Yes-All flag? --> Auto-approve
  |-- macOS Dialog (10s) --> Click karo
  `-- Timeout --> Request file (/tmp/claude-broker/requests/)
                          |
                          v
                Broker Daemon (LaunchAgent)
                          |
                          v
                Telegram Notification
                          |
                          v
                User taps --> Callback
                          |
                          v
                Response file (/tmp/claude-broker/responses/)
                          |
                          v
                Hook reads --> Claude ko return
```

---

## Logs

```bash
tail -f /tmp/claude-broker/broker.log
```

Sample:
```
02:39:11  [telegram] SENT  a5529d00  [my-project/main]
02:41:00  CALLBACK data=instruct:a5529d00
02:41:17  [telegram] INSTRUCT  a5529d00  waiting for text
02:41:24  [instruction] SENT  a5529d00  text=mkdir ki jagah touch use karo
```

---

## Troubleshooting

**Broker nahi chal raha:**
```bash
cbroker
# ya
launchctl load ~/Library/LaunchAgents/com.claude.broker.plist
```

**Telegram callbacks nahi aa rahe (allowed_updates issue):**
```bash
python3 ~/.claude/claude_remote.py setup  # re-run karo
```

**Yes-All clear karna:**
```bash
cclear
```

---

## File Locations

```
~/.claude/claude_remote.py                          # main script
~/.claude/remote_config.json                        # token + chat_id
~/Library/LaunchAgents/com.claude.broker.plist      # auto-start
/tmp/claude-broker/broker.log                       # logs
/tmp/claude-broker/pending.json                     # pending state
```

---

## GitHub

[hikedigitalagency/claude-gate](https://github.com/hikedigitalagency/claude-gate)
