---
description: Recover context from recent conversation
argument-hint: "[lastN | around TIME | search KEYWORD [--all|--global|--project NAME|--tag NAME] | sessions [--all|--project NAME] | session ID ARGS | tag NAME | tags | stats | highlight | connect | disconnect | inbox | config | prune | export | ... (see full list below)]"
allowed-tools: Bash(python3:*), AskUserQuestion
---

# Context Recall

The user wants to recover context from this conversation.

## Step 1: Check for Quick Commands

**FIRST**, check if `$ARGUMENTS` contains a quick command and run it directly, then stop:

> **Session/project are auto-resolved.** Current-session commands resolve the
> session from the native `$CLAUDE_CODE_SESSION_ID` (per-session, so concurrent
> sessions never cross), and current-project commands derive the project from the
> working directory — no `$SESSION_ID`/`$SESSION_HASH` plumbing needed. Commands
> that act on a specific session pass `$CLAUDE_CODE_SESSION_ID` explicitly.

- `lastN` (e.g. `last5`, `last10`, `last20` — any positive N) → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py lastN`
- `around <time>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py around <time>`
- `search <keyword>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py search <keyword>`
- `search <keyword> --all` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py search <keyword> --all`
- `search <keyword> --global` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py search <keyword> --global`
- `search <keyword> --project <name>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py search <keyword> --project <name>`
- `search --tag <name>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_tags.py search <name>`
- `sessions` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py list`
- `sessions --all` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py list --all`
- `sessions --project <name>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py list --project <name>`
- `session <id> <args>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session <id> <args>`
- `tag <name>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_tags.py add <name> $CLAUDE_CODE_SESSION_ID`
- `tag <name> #<exchange>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_tags.py add <name> $CLAUDE_CODE_SESSION_ID <exchange>`
- `tags` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_tags.py list`
- `stats` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py stats`
- `prune --session <id>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py prune --session <id>`
- `prune --before <date>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py prune --before <date>`
- `export --session <id>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py export --session <id>` (always emits JSON)
- `highlight "summary"` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/highlight.py $CLAUDE_CODE_SESSION_ID "summary"`
- `connect <session-id> "topic"` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_connections.py connect $CLAUDE_CODE_SESSION_ID <session-id> "topic"`
- `connect --latest "topic"` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_connections.py connect-latest $CLAUDE_CODE_SESSION_ID "topic"`
- `disconnect <session-id>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_connections.py disconnect $CLAUDE_CODE_SESSION_ID <session-id>`
- `inbox` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_connections.py inbox $CLAUDE_CODE_SESSION_ID`
- `config <key> <value>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_connections.py config $CLAUDE_CODE_SESSION_ID <key> <value>`

**Notes on `--project`:**

- For `search ... --project <name>` and `sessions --project <name>`, `<name>` is matched as an **unanchored substring** against the stored project path (case-sensitive `LIKE '%name%'`). Any session whose project path contains the substring matches.
- For `tags --project <hash>`, the argument is a **project HASH** (exact match), not a name/path. This is distinct from `sessions --project <name>`, which takes a name/path.

If no arguments: Continue to Step 2.

---

## Step 2: Show Conversation Index

Here is the timestamped index of all exchanges in this session:

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/show_index.py`

## Step 3: Present Menu

Now that the user can see the index above, use **AskUserQuestion** to let them choose what to recall:

**Question**: "What would you like to recall?"

**Options** (use these exact labels):
1. **Recent (last 5)** - "Quick recall of the most recent exchanges"
2. **Search by keyword** - "Find exchanges containing specific text"
3. **Jump to time** - "Find exchanges around a specific time (e.g., '2pm')"

## After User Selects

### If "Recent (last 5)":
Run: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py last5`

### If "Search by keyword":
1. Ask for the keyword using AskUserQuestion
2. Run: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py search <keyword>`
3. The script will fetch and display matching exchanges (up to 10 most recent)

### If "Jump to time":
1. Ask what time using AskUserQuestion (e.g., "2pm", "11:30am", "14:30")
2. Run: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py around <time>`
3. The script will fetch exchanges around that time

## After Fetching

Once you've fetched the selected exchanges, provide a brief summary:
- What was being discussed
- Where we left off
- Any pending items

Ask the user to confirm your understanding before continuing.

---

## Direct Fetch (with arguments)

If `$ARGUMENTS` was provided, skip the menu and fetch directly:

**Examples:**
- `/recall last5` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py last5`
- `/recall last10` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py last10`
- `/recall around 2pm` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py around 2pm`
- `/recall search auth` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py search auth`
- `/recall sessions` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py list`
- `/recall tags` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_tags.py list`
- `/recall stats` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py stats`

Run the appropriate script based on `$ARGUMENTS` as described in Step 1.

Then summarize the fetched content and ask user to confirm understanding.

