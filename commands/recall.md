---
description: Recover context from recent conversation
argument-hint: "[last5 | around TIME | search KEYWORD [--all|--global] | sessions | tags | stats | ...]"
allowed-tools: Bash(python3:*), AskUserQuestion
---

# Context Recall

The user wants to recover context from this conversation.

## Step 1: Check for Quick Commands

**FIRST**, check if `$ARGUMENTS` contains a quick command and run it directly, then stop:

- `last5` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID last5`
- `last10`, etc. → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID last10`
- `around <time>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID around <time>`
- `search <keyword>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID search <keyword>`
- `search <keyword> --all` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID --project-hash $SESSION_HASH search <keyword> --all`
- `search <keyword> --global` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py search <keyword> --global`
- `search <keyword> --project <name>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py search <keyword> --project <name>`
- `search --tag <name>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_tags.py search <name>`
- `sessions` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py list $SESSION_HASH`
- `sessions --all` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py list --all`
- `sessions --project <name>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py list --project <name>`
- `session <id> <args>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session <id> <args>`
- `tag <name>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_tags.py add <name> $SESSION_ID`
- `tag <name> #<exchange>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_tags.py add <name> $SESSION_ID <exchange>`
- `tags` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_tags.py list $SESSION_HASH`
- `stats` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py stats`
- `prune --session <id>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py prune --session <id>`
- `prune --before <date>` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py prune --before <date>`
- `export --session <id> --json` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py export --session <id>`

If no arguments: Continue to Step 2.

---

## Step 2: Show Conversation Index

Here is the timestamped index of all exchanges in this session:

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/show_index.py --session $SESSION_ID`

## Step 3: Present Menu

Now that the user can see the index above, use **AskUserQuestion** to let them choose what to recall:

**Question**: "What would you like to recall?"

**Options** (use these exact labels):
1. **Recent (last 5)** - "Quick recall of the most recent exchanges"
2. **Search by keyword** - "Find exchanges containing specific text"
3. **Jump to time** - "Find exchanges around a specific time (e.g., '2pm')"

## After User Selects

### If "Recent (last 5)":
Run: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID last5`

### If "Search by keyword":
1. Ask for the keyword using AskUserQuestion
2. Run: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID search <keyword>`
3. The script will fetch and display matching exchanges (up to 10 most recent)

### If "Jump to time":
1. Ask what time using AskUserQuestion (e.g., "2pm", "11:30am", "14:30")
2. Run: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID around <time>`
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
- `/recall last5` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID last5`
- `/recall last10` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID last10`
- `/recall around 2pm` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID around 2pm`
- `/recall search auth` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_exchanges.py --session $SESSION_ID search auth`
- `/recall sessions` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py list $SESSION_HASH`
- `/recall tags` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_tags.py list $SESSION_HASH`
- `/recall stats` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_sessions.py stats`

Run the appropriate script based on `$ARGUMENTS` as described in Step 1.

Then summarize the fetched content and ask user to confirm understanding.
