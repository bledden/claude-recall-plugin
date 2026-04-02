# Recall Assistant Skill — Behavioral Eval Script

Run these manually in a Claude Code session with the plugin loaded.
Record PASS/FAIL for each scenario.

## Setup

```
# Load the plugin
claude --plugin-dir /Users/bledden/Documents/claude-recall-plugin

# Enable the skill
/recall config skill_enabled true
```

---

## Eval 1: Skill Gate (CRITICAL)

### 1a. Skill disabled — no proactive behavior
```
/recall config skill_enabled false
```
Then say: "I think we discussed warp divergence earlier"

**Expected:** Claude responds normally, does NOT suggest /recall.
**PASS/FAIL:** ___

### 1b. Skill enabled — proactive behavior
```
/recall config skill_enabled true
```
Then say: "I think we discussed warp divergence earlier"

**Expected:** Claude suggests `/recall search "warp divergence"` or similar.
**PASS/FAIL:** ___

---

## Eval 2: Explicit Context-Loss Signals

### 2a. "Didn't we already..."
Say: "Didn't we already talk about the shared memory approach?"

**Expected:** Claude acknowledges potential context loss and suggests a recall command.
**PASS/FAIL:** ___

### 2b. "What was that thing..."
Say: "What was that thing you suggested about occupancy?"

**Expected:** Claude suggests `/recall search "occupancy"` or similar.
**PASS/FAIL:** ___

### 2c. "Earlier you said..."
Say: "Earlier you said something about warp shuffle — what was it?"

**Expected:** Claude suggests a recall search rather than guessing.
**PASS/FAIL:** ___

---

## Eval 3: Behavioral Context-Loss Signals

### 3a. Contradiction detection
First, establish a fact: "The optimal threadgroup size for this kernel is 256."
Then later say: "What threadgroup size should I use?"
If Claude suggests a different number without checking recall:

**Expected:** Claude pauses and suggests checking recall before answering.
**PASS/FAIL:** ___
**Note:** This is the hardest signal to trigger. Record what actually happens.

---

## Eval 4: Temporal Context-Loss Signals

### 4a. PostCompact nudge
If a PostCompact nudge fires during the session:

**Expected:** Claude proactively suggests recovering context via /recall.
**PASS/FAIL:** ___
**Note:** This requires a long enough session for compaction to trigger.

---

## Eval 5: Proactive Highlighting (default — suggest)

### 5a. Solution-worthy finding
Ensure skill is enabled and session has a connection:
```
/recall config skill_enabled true
/recall connect --latest "kernel work"
```
Then ask Claude to solve a kernel optimization problem. When it produces a solution:

**Expected:** Claude suggests `/recall highlight "summary of finding"` and asks for confirmation.
**PASS/FAIL:** ___

### 5b. Routine answer — no highlight
Ask Claude a simple question: "What does __syncthreads() do in CUDA?"

**Expected:** Claude answers without suggesting a highlight. (This is not a transferable finding.)
**PASS/FAIL:** ___

---

## Eval 6: Proactive Highlighting (auto_run)

### 6a. Auto-run enabled
```
/recall config auto_run_highlight true
```
Ask Claude to solve a problem. When it produces a solution:

**Expected:** Claude runs `/recall highlight` automatically and briefly notes what it flagged.
**PASS/FAIL:** ___

### 6b. Auto-run disabled again
```
/recall config auto_run_highlight false
```
Ask Claude to solve another problem.

**Expected:** Claude suggests the highlight but waits for confirmation.
**PASS/FAIL:** ___

---

## Eval 7: Connection Suggestions

### 7a. Natural language connect
Say: "Keep an eye on session abc123, they're working on CUDA kernels"

**Expected:** Claude suggests `/recall connect abc123 "CUDA kernels"` or runs it.
**PASS/FAIL:** ___

### 7b. No session ID provided
Say: "My other session is working on related kernel stuff, can you watch it?"

**Expected:** Claude suggests `/recall connect --latest "kernel work"` or asks for the session ID.
**PASS/FAIL:** ___

### 7c. Does not auto-run connect
Say: "Link me to that other session"

**Expected:** Claude suggests the command but does NOT run it without confirmation.
**PASS/FAIL:** ___

---

## Eval 8: Inbox Awareness

### 8a. Topic overlap
Ensure session has connections:
```
/recall connect --latest "kernel optimization"
```
Then start discussing kernel optimization:
"Let's work on optimizing the reduction kernel's memory access pattern."

**Expected:** Claude suggests checking `/recall inbox` before diving in.
**PASS/FAIL:** ___

### 8b. Does not auto-run inbox
**Expected:** Claude suggests the command but does NOT run it automatically.
**PASS/FAIL:** ___

---

## Eval 9: Signal Configuration

### 9a. Disable behavioral signals
```
/recall config detection_signals explicit,temporal
```
Then trigger a behavioral signal (e.g., Claude contradicting itself):

**Expected:** Claude does NOT suggest recall based on behavioral signal.
**PASS/FAIL:** ___
**Note:** This is hard to test reliably. Record observations.

### 9b. Re-enable all signals
```
/recall config detection_signals explicit,behavioral,temporal
```

---

## Eval 10: No False Positives

### 10a. Normal conversation
Have a normal 5-exchange conversation about a technical topic.

**Expected:** Claude does NOT suggest /recall, /highlight, /connect, or /inbox unprompted unless a genuine signal fires.
**PASS/FAIL:** ___
**Count false positives:** ___

### 10b. Short session
In a fresh session with skill enabled, have 3 exchanges.

**Expected:** No temporal signals fire (session too short). No proactive suggestions unless explicit signals are present.
**PASS/FAIL:** ___

---

## Results Summary

| Eval | Description | Result |
|---|---|---|
| 1a | Skill disabled gate | |
| 1b | Skill enabled gate | |
| 2a | "Didn't we already..." | |
| 2b | "What was that thing..." | |
| 2c | "Earlier you said..." | |
| 3a | Contradiction detection | |
| 4a | PostCompact nudge | |
| 5a | Solution highlight (suggest) | |
| 5b | Routine answer (no highlight) | |
| 6a | Auto-run highlight | |
| 6b | Auto-run disabled | |
| 7a | Natural language connect | |
| 7b | No session ID | |
| 7c | No auto-run connect | |
| 8a | Topic overlap inbox | |
| 8b | No auto-run inbox | |
| 9a | Signal config | |
| 10a | No false positives (normal) | |
| 10b | No false positives (short) | |
