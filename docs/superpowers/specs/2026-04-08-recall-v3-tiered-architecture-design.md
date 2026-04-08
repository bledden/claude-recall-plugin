# Recall Plugin v3.0.0 — Tiered Architecture & Semantic Search Design

**Date:** 2026-04-08
**Author:** Blake Ledden
**Status:** Draft
**Extends:** Recall Plugin v2.2.0

## Overview

Recall v3.0.0 introduces a three-tier runtime architecture (Lite, Standard, Enhanced) and an opt-in semantic search layer powered by local embeddings. The tiers let users choose their footprint: bare-minimum per-session recall, the full cross-session experience, or ML-enhanced semantic retrieval. The embedding engine uses a platform-aware fallback chain (MLX, sentence-transformers, ONNX Runtime, TF-IDF) that auto-detects the best available backend per device.

**Target users:**
- **Lite:** Users who run one session at a time and want minimal overhead.
- **Standard:** Power users who want cross-session search, tags, and sharing (current v2.2.0 audience).
- **Enhanced:** Users who want semantic search, smarter compaction recovery, and semantic highlight matching — and are willing to install an embedding backend.

## Goals

1. **Three runtime tiers** — Lite (JSON, per-session), Standard (SQLite + FTS5), Enhanced (SQLite + FTS5 + embeddings)
2. **Platform-aware embedding** — best backend auto-detected per platform (MLX on Apple Silicon, ONNX/sentence-transformers elsewhere, TF-IDF as floor)
3. **Opt-in Enhanced tier** — no model downloads or ML dependencies unless the user explicitly activates Enhanced
4. **Checkpoint-based embedding** — vectors computed only during PostCompact and SessionEnd events, never on every prompt
5. **Hybrid search** — Enhanced tier combines FTS5 keyword results with cosine similarity for better retrieval
6. **Smarter compaction recovery** — Enhanced tier surfaces the most relevant prior exchanges after compaction, not just the most recent
7. **Silent surfacing plumbing** — architecture supports proactive context injection per-prompt, shipped disabled pending accuracy validation
8. **Non-destructive tier transitions** — switching tiers never deletes data

## Non-Goals

- Indexing user files or codebases (that's searchlight's domain)
- Forcing any ML dependency on any user
- Shipping silent surfacing as enabled
- Real-time push between sessions
- Auto-upgrading existing users to Enhanced tier

---

## Tier System

### Tier Definitions

| Tier | Storage | Search | Features | Dependencies |
|---|---|---|---|---|
| **Lite** | JSON file (`index.json`) | In-memory keyword | Current-session recall, `/clear` survival, basic index | Python stdlib only |
| **Standard** | SQLite + FTS5 (`recall.db`) | Full-text keyword | Cross-session, cross-project, tags, sharing, compaction nudge, auto-tagging | Python stdlib only |
| **Enhanced** | SQLite + FTS5 + vectors (`recall.db`) | Full-text + semantic | Everything in Standard + semantic search, smart compaction recovery, semantic highlight matching, silent surfacing (disabled) | One of: mlx-embedding-models, sentence-transformers, onnxruntime, or scikit-learn |

### Configuration

Global config stored in `~/.claude/context-recall/recall-config.json` (not per-session, since tier affects storage backend):

```json
{
  "tier": "standard",
  "embedding_backend": "auto",
  "detected_backend": "mlx",
  "model_version": "bge-small-en-v1.5",
  "model_hash": null,
  "silent_surfacing": false
}
```

**User-facing config keys** (shown in `/recall config` help):
- `tier` — `lite`, `standard`, or `enhanced`
- `embedding_backend` — `auto` (default), `mlx`, `sentence-transformers`, `onnx`, or `tfidf`

**Advanced config keys** (settable but not prominently documented):
- `search_keyword_weight` — FTS5 score weight in hybrid ranking (default: `0.4`)
- `search_semantic_weight` — cosine similarity weight in hybrid ranking (default: `0.6`)
- `checkpoint_batch_size` — max exchanges to embed per checkpoint event (default: `200`)
- `surfacing_threshold` — minimum cosine similarity for silent surfacing (default: `0.75`)
- `surfacing_max_results` — max exchanges surfaced per prompt (default: `3`)
- `surfacing_recency_exclude` — skip N most recent exchanges in surfacing (default: `5`)
- `embed_on_compact` — embed un-vectorized exchanges during PostCompact (default: `true`)
- `embed_on_session_end` — embed un-vectorized exchanges during SessionEnd (default: `true`)

### Tier Transitions

- **Lite -> Standard:** Creates `recall.db`, migrates JSON index into SQLite (existing v1->v2 migration path).
- **Standard -> Enhanced:** User must explicitly activate. Triggers model download (with notification). Existing un-vectorized exchanges are backfilled in batches across subsequent checkpoint events.
- **Enhanced -> Standard:** Vectors ignored, FTS5 used for search. No data deleted. Model stays cached.
- **Standard -> Lite:** SQLite retained on disk but unused. JSON file becomes active storage.
- **Enhanced -> Lite:** Same as above. Vectors and SQLite retained, unused.

All transitions are non-destructive. Downgrading never deletes data.

---

## Install Flow & First Run

### New Users

On first run (no `recall-config.json` exists):

1. Probe available embedding backends (see Detection Order below).
2. Determine highest viable tier:
   - Any embedding backend found -> suggest Enhanced
   - Python only -> suggest Standard
3. Present options via hook systemMessage:
   ```
   [recall] Welcome! Detected embedding backend: mlx

   Available tiers:
   - lite: Per-session only, minimal footprint
   - standard: Cross-session search, tags, sharing (SQLite)
   - enhanced: Semantic search, smart context recovery (requires model download)

   Recommended: enhanced

   To choose your tier: /recall config tier <lite|standard|enhanced>
   Current default: standard (no model download required)
   ```
4. Default is **Standard** until the user explicitly chooses otherwise. This ensures no model download happens without consent.

### Existing Users (v2.2.0 Upgrade)

Existing users have `recall.db` but no `recall-config.json`. On first run of v3:

1. Detect existing `recall.db` -> user is at least Standard.
2. Probe embedding backends.
3. Write config with `"tier": "standard"` (do not auto-upgrade to Enhanced).
4. Inform user:
   ```
   [recall] Upgraded to v3.0.0. Current tier: standard.
   Embedding backend available: mlx

   To enable semantic search: /recall config tier enhanced
   ```

Existing users opt in. No changes to their current experience unless they choose to upgrade.

---

## Embedding Engine

### Module: `scripts/embeddings.py`

Standalone module. No knowledge of recall's DB, hooks, or sessions.

### Public Interface

```python
def is_available() -> bool:
    """Whether any embedding backend is installed."""

def get_backend_name() -> str:
    """Returns 'mlx' | 'sentence-transformers' | 'onnx' | 'tfidf' | 'none'"""

def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns 384-dim vectors."""

def embed_query(text: str) -> list[float]:
    """Embed a single query. Cached via LRU (256 entries)."""
```

### Detection Order

On first call, probe once and cache result to `recall-config.json`:

1. **MLX** — `import mlx_embedding_models` + verify Apple Silicon. Model: `bge-small` (384-dim). ~4-10ms per query on Metal GPU.
2. **sentence-transformers** — `import sentence_transformers`. Model: `BAAI/bge-small-en-v1.5` (384-dim). Only preferred if already installed (PyTorch present). ~10-20ms per query.
3. **ONNX Runtime** — `import onnxruntime`. Model: `BAAI/bge-small-en-v1.5` exported to ONNX. ~15-30ms per query on CPU. Hardware-specific backends: CUDA (NVIDIA), DirectML (Windows GPU), CoreML (macOS Intel).
4. **TF-IDF** — `from sklearn.feature_extraction.text import TfidfVectorizer`. No model download. Builds vocabulary from exchange data. ~1ms per query.
5. **None** — no backend available. `is_available()` returns `False`. Enhanced tier unavailable.

### Platform Recommendations

| Platform | Recommended Backend | Fallback |
|---|---|---|
| macOS Apple Silicon | MLX | sentence-transformers -> ONNX -> TF-IDF |
| macOS Intel | ONNX (CoreML) | sentence-transformers -> TF-IDF |
| Windows (NVIDIA) | ONNX (CUDA) | sentence-transformers -> TF-IDF |
| Windows (AMD/Intel) | ONNX (DirectML) | sentence-transformers -> TF-IDF |
| Linux (NVIDIA) | ONNX (CUDA) | sentence-transformers -> TF-IDF |
| Linux (CPU) | ONNX | sentence-transformers -> TF-IDF |

### Model

All ML backends use **`BAAI/bge-small-en-v1.5`** — 384 dimensions, ~33MB. Small enough to load/unload quickly per checkpoint, accurate enough for conversation-length text matching.

### Model Download

Download occurs **only** when the user activates Enhanced tier:

- **MLX / sentence-transformers:** Libraries auto-download from HuggingFace Hub on first embed call. Model caches to `~/.cache/huggingface/`.
- **ONNX:** Plugin downloads pre-exported ONNX model via Python stdlib `urllib` to `~/.claude/context-recall/models/bge-small-en-v1.5.onnx`.
- **TF-IDF:** No download needed.
- **Offline, no cached model:** Degrade to TF-IDF with warning: `[recall] Embedding model unavailable (offline?). Using keyword fallback. Model will download on next connection.`

User sees: `[recall] Downloading embedding model (33MB, one-time)...` followed by `[recall] Model installed. Enhanced tier active.`

### Model Updates

When a plugin update bumps the required model version:

1. Plugin code has a `REQUIRED_MODEL_VERSION` constant.
2. On next hook run, compare against `model_version` in config.
3. If mismatch, notify (do not auto-download):
   ```
   [recall] A newer embedding model is available. Run /recall config update-model to upgrade.
   ```
4. User runs command -> download new model -> old vectors deleted from `exchange_vectors` -> re-embedded at next checkpoint.
5. User ignores -> old model continues working indefinitely.

### Thread Safety

Single `threading.Lock` around all embed calls (required for MLX Metal backend thread safety). Only relevant during checkpoint batches; single-exchange embeds are fast enough that contention is a non-issue.

### Resource Lifecycle

- Model loads lazily on first `embed()` call within a checkpoint.
- Model unloads when the hook process exits (hooks are short-lived processes).
- No persistent GPU memory between hook invocations.
- Query cache (LRU, 256 entries) is per-process and resets between invocations.

---

## Storage Changes

### Enhanced Tier Schema

One new table in `recall.db`:

```sql
CREATE TABLE IF NOT EXISTS exchange_vectors (
    exchange_id     INTEGER PRIMARY KEY REFERENCES exchanges(id),
    vector          BLOB NOT NULL,
    backend         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
```

- `vector`: 384-dim float32 stored as raw bytes (1,536 bytes per row).
- `backend`: which backend produced this vector. Used for backend mismatch detection.

### Why Not HNSW

Searchlight needs HNSW because it indexes ~2.85M file chunks. Recall's dataset is fundamentally smaller. Even a power user with 50 sessions averaging 100 exchanges has 5,000 vectors. Brute-force cosine similarity over 5,000 384-dim vectors takes ~1-2ms with numpy. HNSW would add a dependency, an index file, and rebuild logic for no measurable gain at this scale.

If the dataset grows to 100K+ vectors, HNSW can be added behind the same interface without changing callers.

### Storage Cost

| Exchanges | Vector Storage | With SQLite Overhead |
|---|---|---|
| 100 | ~150 KB | ~170 KB |
| 1,000 | ~1.5 MB | ~1.7 MB |
| 10,000 | ~15 MB | ~17 MB |
| 50,000 | ~75 MB | ~85 MB |

### Backend Mismatch Detection

If `detected_backend` in config changes from what's stored in `exchange_vectors.backend`:

1. Log warning: `[recall] Embedding backend changed. Existing vectors will be re-embedded at next checkpoint.`
2. DELETE all rows from `exchange_vectors` (different models produce incompatible embeddings).
3. Next checkpoint re-embeds from scratch.

### Lite Tier Storage

Lite uses the v1.0.1 JSON index approach (`~/.claude/context-recall/index.json`). No `recall.db`, no `exchange_vectors`. Upgrading from Lite -> Standard triggers the existing JSON migration path.

---

## Checkpoint-Based Embedding

Vectors are computed **only** during natural pause points where the user is already waiting. No embedding on every prompt.

### Checkpoint Events

| Event | What Happens | Why This Moment |
|---|---|---|
| **PostCompact** | Embed un-vectorized exchanges for this session (up to batch cap) | Compaction means recall is likely needed soon. User is already waiting through compaction. |
| **SessionEnd** | Embed remaining un-vectorized exchanges | Session is closing. Good housekeeping. Non-blocking. |
| **Standard -> Enhanced upgrade** | Backfill existing exchanges in batches across subsequent checkpoints | One-time transition cost, spread out. |

### What Does NOT Trigger Embedding

- Regular prompts (prompt_submit hook stays fast, no embedding work)
- `/recall` invocation (user wants results now, don't make them wait for embedding)
- Any Lite or Standard tier operation

### Batch Cap

Maximum **200 exchanges** per checkpoint event. If more un-vectorized exchanges exist, the remainder is caught at the next checkpoint. At 200 exchanges per checkpoint:

| Backend | Estimated Time |
|---|---|
| MLX | ~1 second |
| sentence-transformers | ~2 seconds |
| ONNX CPU | ~4-6 seconds |
| TF-IDF | ~0.2 seconds |

All well within the 30-second hook timeout.

### Un-Vectorized Exchange Tracking

Exchanges without a corresponding row in `exchange_vectors` are un-vectorized. Checkpoint triggers query:

```sql
SELECT e.id, e.user_text, e.assistant_text
FROM exchanges e
LEFT JOIN exchange_vectors v ON e.id = v.exchange_id
WHERE e.session_id = ? AND v.exchange_id IS NULL
LIMIT 200
```

No separate tracking column needed.

---

## Search

### Standard Tier (Unchanged)

```
/recall search "query"          -> FTS5 keyword match -> results
/recall search "query" --all    -> FTS5 across project -> results
/recall search "query" --global -> FTS5 across all -> results
```

### Enhanced Tier: Hybrid Search

No new command syntax. Enhanced tier upgrades existing search transparently:

```
User runs: /recall search "GPU thread branching issue"

1. FTS5 keyword search (same as today)          -> keyword_results
2. embed_query("GPU thread branching issue")    -> query_vector
3. Cosine similarity against exchange_vectors    -> semantic_results
4. Merge, deduplicate by exchange ID, rank       -> final_results
```

### Hybrid Ranking

```
final_score = (keyword_weight * fts5_score) + (semantic_weight * cosine_similarity)
```

Default weights: `keyword_weight = 0.4`, `semantic_weight = 0.6`.

Rationale: keyword matches are precise but brittle (miss synonyms, rephrasings). Semantic matches capture meaning but may surface loosely related content. Weighting semantic higher finds more relevant results while keyword acts as a precision boost when exact terms appear. Results appearing in both sets get a natural boost since both scores contribute.

Configurable via:
```
/recall config search_keyword_weight 0.4
/recall config search_semantic_weight 0.6
```

### Scope

The `--all` and `--global` flags expand which `exchange_vectors` rows participate in cosine similarity, same as they expand FTS5 scope.

### Graceful Degradation

| Scenario | Behavior |
|---|---|
| Enhanced, all exchanges vectorized | Full hybrid search |
| Enhanced, some un-vectorized | Semantic over vectorized + FTS5 over all, merged |
| Enhanced, no backend available | FTS5 only with warning |
| Enhanced, zero vectors exist yet | FTS5 only (first checkpoint hasn't fired) |
| Standard | FTS5 only |
| Lite | In-memory keyword search |

### Force Keyword-Only on Enhanced

```
/recall config search_semantic_weight 0
```

---

## Smarter Compaction Recovery

### Current PostCompact Nudge (Standard)

Surfaces the last 5 exchange previews chronologically plus top auto-tags. Effective but not optimal — the most recent exchanges may not be the most relevant to the current work.

### Enhanced PostCompact Nudge

After embedding the checkpoint batch, the PostCompact hook additionally:

1. Compute a "recent topic vector" — average of the last 5 exchange vectors.
2. Cosine similarity of this topic vector against all session exchange vectors.
3. Take the top 5 most relevant exchanges (excluding the last 5 chronological, which are already in the standard nudge).
4. Include in the nudge:

```
[Context Compacted] This session has {N} exchanges indexed.
{M} total exchanges across this project's history.
Recent topics: {tag1}, {tag2}, {tag3}
Last exchanges:
  - "{preview_recent_1}"
  - "{preview_recent_2}"
Related earlier context:
  - #{idx} "{preview_relevant_1}"
  - #{idx} "{preview_relevant_2}"
Use /recall to recover full conversation context.
```

This surfaces exchange #12 from 3 hours ago if it's more relevant to the current work than exchange #95 from 20 minutes ago.

---

## Semantic Highlight Matching

### Current Highlight Check (Standard)

Cross-session highlights are matched by checking `created_at > last_checked_at` — purely temporal. All new highlights from a connected session appear regardless of relevance.

### Enhanced Highlight Check

When checking connections for new highlights, additionally compute cosine similarity between the watcher session's recent topic vector and each highlight's summary (embedded at creation time). Rank highlights by relevance and surface the most relevant first.

This requires embedding highlight summaries when they're created. Add a `summary_vector` BLOB column to the `highlights` table.

**Schema migration:** For new installs, `summary_vector BLOB` is included in the `highlights` CREATE TABLE DDL. For existing users upgrading to v3, a one-time migration adds the column:

```sql
ALTER TABLE highlights ADD COLUMN summary_vector BLOB;
```

This migration runs in `get_connection()` alongside schema version detection (see Storage Changes).

When a highlight is created and Enhanced tier is active, embed the summary and store the vector. When checking connections, compare against the watcher's topic vector. Highlights below a relevance threshold (default: 0.5) are still delivered but ranked lower.

---

## Silent Surfacing (Specced, Disabled)

### Concept

On each prompt, find prior exchanges relevant to the current conversation and inject them as system context so Claude has the right background without the user running `/recall`.

### Why Disabled

High accuracy bar. Bad surfacing (irrelevant context injected) wastes tokens, confuses Claude, and erodes trust. Needs tuning against real usage patterns before shipping enabled.

### Plumbing Built Now

**Config:**
```
/recall config silent_surfacing true   # default: false
```

**Hook point:** `prompt_submit.py`, after exchange insertion, before returning.

**Data flow (when enabled):**

1. Get current `user_prompt` text.
2. `embed_query(user_prompt)` -> `query_vector`.
3. Cosine similarity against session's `exchange_vectors`.
4. Filter: similarity > `surfacing_threshold` (default 0.75), exclude last `surfacing_recency_exclude` (default 5) exchanges.
5. Take top `surfacing_max_results` (default 3) results.
6. Format as systemMessage:
   ```
   [Recall] Potentially relevant prior context:
     - Exchange #12 (Jan 5, 2:30pm): "{preview}"
     - Exchange #87 (Jan 6, 11am): "{preview}"
   Run /recall search to pull full content.
   ```

**Cost when enabled:** `embed_query()` call (~4-10ms if model cached, longer on cold load) + brute-force search (~1ms). No new embedding work — uses pre-computed vectors from checkpoints.

### Known Constraint: Model Loading

Hooks are short-lived processes. Silent surfacing would require loading the embedding model on every prompt (~50-500ms depending on backend) since the process exits between invocations. This is acceptable for infrequent checkpoint events but too costly for every prompt.

**Solutions to explore before enabling (not in this spec):**
- Pre-computed relevance caches built at checkpoint time
- Model memory-mapping for near-instant loads (MLX supports this)
- Disk-cached query embeddings
- Persistent embedding server process

The architecture supports all of these without structural changes because the silent surfacing hook point is isolated behind a config gate.

---

## Hook Changes

### hooks.json

Timeout increase for PostCompact and SessionEnd to accommodate embedding batches:

```json
{
  "PostCompact": [{ "timeout": 30 }],
  "SessionEnd": [{ "timeout": 30 }]
}
```

Lite and Standard tiers exit these hooks in <100ms. The increased timeout only matters for Enhanced tier checkpoint batches.

`UserPromptSubmit` timeout stays at 10 seconds — no embedding work happens here.

### prompt_submit.py Changes

1. **First-run detection:** Check for `recall-config.json`, run setup flow if absent.
2. **Tier routing:** Early branch on `tier` value — Lite uses JSON path, Standard/Enhanced use SQLite path.
3. **Silent surfacing plumbing:** After exchange insertion, if `silent_surfacing` is enabled, run the surfacing query. Disabled by default; code path exists but never executes.
4. **No embedding work.** This hook stays fast on every prompt regardless of tier.

### post_compact.py Changes

Enhanced tier additions after existing nudge logic:

1. Query un-vectorized exchanges for this session (up to batch cap).
2. If un-vectorized count > 0: load embedding engine, embed batch, insert into `exchange_vectors`, unload.
3. Build smarter nudge: compute recent topic vector, find most relevant earlier exchanges, include in nudge alongside chronological previews.

Standard and Lite tiers: no change to existing behavior.

### session_end.py Changes

Enhanced tier addition after existing `ended_at` update:

1. If un-vectorized exchanges exist: load embedding engine, embed remaining (up to batch cap), insert into `exchange_vectors`, unload.

Standard and Lite tiers: no change.

---

## File Structure

### New Files

```
scripts/embeddings.py           # Embedding engine (4-tier fallback, detect, embed, cache)
scripts/vector_search.py        # Hybrid search (FTS5 + cosine, merge, rank)
tests/test_embeddings.py        # Backend detection, embed, cache, thread safety
tests/test_vector_search.py     # Hybrid ranking, degradation, scope
```

### Modified Files

```
scripts/db.py                   # exchange_vectors table, CRUD, backend mismatch, highlights summary_vector
hooks/prompt_submit.py          # First-run detection, tier routing, silent surfacing plumbing
hooks/post_compact.py           # Checkpoint embedding + smarter nudge
hooks/session_end.py            # Checkpoint embedding on close
hooks/hooks.json                # Timeout bump on post_compact and session_end (5s -> 30s)
scripts/fetch_exchanges.py      # Hybrid search when tier == enhanced
scripts/highlight.py            # Embed highlight summary on creation (Enhanced)
scripts/manage_connections.py   # tier/embedding config commands
commands/recall.md              # Document new config options, tier system
.claude-plugin/plugin.json      # Bump to 3.0.0
README.md                       # Tier documentation, install flow, embedding setup
```

### Unchanged Files

```
scripts/utils.py
scripts/auto_tagger.py
scripts/manage_tags.py
scripts/manage_sessions.py
scripts/show_index.py
skills/recall-assistant/SKILL.md
```

### New Config File

```
~/.claude/context-recall/recall-config.json
```

### Model Cache Directory

```
~/.claude/context-recall/models/
```

---

## Testing Strategy

### New Test Files

**`test_embeddings.py`:**
- Backend detection order with mocked imports
- Each backend (MLX, sentence-transformers, ONNX, TF-IDF) produces 384-dim vectors
- No backend available returns `is_available() == False`
- LRU cache returns same vector without re-embedding
- Thread lock serializes concurrent embed calls
- Graceful degradation when model not downloaded (offline)

**`test_vector_search.py`:**
- Hybrid ranking: keyword-only, semantic-only, both-match ranked highest
- Configurable weights shift ranking correctly
- No vectors -> FTS5 only (graceful degradation)
- Scope: session, project, global produce correct candidate sets
- Backend mismatch triggers vector deletion
- Empty query returns empty results

### Modified Test Files

**`test_post_compact.py`:**
- Enhanced tier: checkpoint embeds un-vectorized exchanges
- Enhanced tier: smarter nudge uses relevance, not just recency
- Batch cap respected (>200 un-vectorized only processes 200)
- Standard/Lite: existing behavior preserved

**`test_session_end.py`:**
- Enhanced tier: embeds remaining exchanges on close
- Batch cap respected
- Standard/Lite: no change

**`test_prompt_submit.py`:**
- First-run config generation and tier suggestion
- Tier routing (Lite -> JSON, Standard/Enhanced -> SQLite)
- Silent surfacing plumbing (disabled path, enabled path with mock vectors)
- No embedding work happens in this hook

**`test_highlight.py`:**
- Enhanced tier: highlight creation embeds summary
- Standard tier: no embedding, summary_vector is NULL

### Existing Tests

All 226 existing tests continue to pass unchanged. Standard tier behavior is identical to v2.2.0.

### Lite Tier Tests

Dedicated test coverage for the JSON storage path:
- Single-session indexing and retrieval
- `/clear` survival
- Basic keyword search
- Upgrade to Standard triggers migration

---

## Security

### Inherited Practices (v2.2.0)

All existing security practices carry forward:
- Parameterized SQL for all queries (no string interpolation)
- LIKE wildcards escaped in `list_sessions()` to prevent pattern injection
- Stdin reads bounded to 1MB per hook invocation
- Database directory created with restricted permissions (0o700)
- Error messages sanitized to avoid leaking file paths or internal state
- No dynamic code execution (no eval, exec, or unsafe deserialization in plugin code)
- No external network requests in Lite or Standard tiers

### New Attack Surface (Enhanced Tier)

**Model download integrity:**
- All model downloads verified against a SHA256 hash hardcoded in the plugin source (REQUIRED_MODEL_HASH constant).
- If hash verification fails, the download is discarded and the tier falls back to TF-IDF with a warning.
- Downloads use HTTPS only. No HTTP fallback.
- The model URL is hardcoded in the plugin, not configurable by user input or environment variables, to prevent redirect attacks.

**Model file storage:**
- Model files are stored in ~/.claude/context-recall/models/ with directory permissions 0o700.
- Model file paths are constructed internally from hardcoded filenames. No user input is used in path construction.
- The plugin never loads arbitrary model files from user-specified paths.

**Third-party deserialization risks:**
- sentence-transformers / PyTorch: Uses unsafe deserialization internally for model loading. This is a known arbitrary code execution vector if the model file is compromised. Mitigation: SHA256 hash verification of downloaded model files. Users who install sentence-transformers accept PyTorch's trust model.
- ONNX Runtime: Uses protobuf for model loading (safer). Lower risk.
- MLX: Uses safetensors format by default. Safe.
- TF-IDF: No model file. Zero deserialization risk.
- The plugin documents which backends use unsafe deserialization so users can make informed choices about their embedding_backend setting.

**Vector storage:**
- Vectors stored as BLOBs via parameterized INSERT. No dynamic SQL in vector search path.
- Cosine similarity computed in Python (numpy) over fetched BLOBs. No SQL-level vector operations that could be injected.

**TF-IDF corpus:**
- TF-IDF builds vocabulary from the user's own conversation exchanges. Since exchanges come from the local Claude transcript (not external input), corpus poisoning requires the user's own session to be compromised, at which point the attacker has larger problems.
- TF-IDF corpus capped at 10,000 entries to bound memory usage.

**Config file security:**
- recall-config.json stores tier and backend settings. Written only during setup or explicit config commands.
- Config values are validated against allowlists before use (e.g., tier must be one of lite, standard, enhanced).
- No config value is used in shell commands, SQL queries, or file path construction without validation.

### Plugin Exploit Mitigations

Given recent plugin security concerns:
- No outbound network calls except the one-time model download during Enhanced tier activation (HTTPS only, hash-verified, hardcoded URL).
- No code execution from downloaded content. Model files are loaded by ML libraries' inference engines, not executed as scripts.
- No eval or exec anywhere in the plugin codebase. All hook scripts are static Python.
- No environment variable injection. The plugin reads CLAUDE_PLUGIN_ROOT, SESSION_ID, SESSION_HASH from the hook environment but never interpolates them into shell commands or SQL.
- Hook stdin is untrusted. All JSON from stdin is parsed with json.loads (safe) and individual fields are type-checked before use.

## Version

Plugin bumps from 2.2.0 to 3.0.0. Scope justifies major version:
- New tier system changes runtime behavior
- New storage table
- New external dependency story (opt-in)
- New config file and install flow
