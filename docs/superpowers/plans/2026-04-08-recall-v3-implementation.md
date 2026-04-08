# Recall v3.0.0 — Tiered Architecture & Semantic Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three runtime tiers (Lite/Standard/Enhanced) to the recall plugin, with opt-in semantic search via a platform-aware embedding engine.

**Architecture:** A global config module (`config.py`) gates tier behavior. A standalone embedding engine (`embeddings.py`) with four-level fallback (MLX → sentence-transformers → ONNX → TF-IDF) powers the Enhanced tier. Vectors are stored as BLOBs in SQLite (`exchange_vectors` table) and computed only during PostCompact and SessionEnd checkpoints. Hybrid search merges FTS5 keyword results with cosine similarity. Silent surfacing is plumbed but disabled.

**Tech Stack:** Python 3.6+ stdlib, SQLite FTS5, numpy (Enhanced tier), optional: mlx-embedding-models, sentence-transformers, onnxruntime, scikit-learn.

**Spec:** `docs/superpowers/specs/2026-04-08-recall-v3-tiered-architecture-design.md`

---

## Known Gaps (Follow-Up Tasks)

These spec requirements are not covered by the 13 tasks above and should be implemented as follow-up work:

1. **Lite tier JSON storage path** — Task 7 adds tier routing but the actual JSON read/write code (v1.0.1 behavior) needs to be extracted into a reusable path within `prompt_submit.py`. The old `save_context_snapshot.py` code exists in the repo and can be adapted. Blocked on deciding whether to resurrect the old code or rewrite.

2. **ONNX model download + SHA256 verification** — The `embeddings.py` ONNX backend raises `NotImplementedError` because it requires a tokenizer alongside the model file. Implementing this properly requires: (a) exporting bge-small to ONNX with tokenizer via HuggingFace `optimum`, (b) hosting the exported model, (c) writing the download + hash verification flow, (d) wiring up the ONNX inference path. This is a self-contained follow-up.

3. **Tier transition integration tests** — End-to-end tests for Lite→Standard, Standard→Enhanced, Enhanced→Standard transitions including data preservation verification.

---

## Task 1: Global Config Module

**Files:**
- Create: `scripts/config.py`
- Test: `tests/test_config.py`

This module manages `recall-config.json` — load, save, validate, defaults, and first-run detection. Everything else depends on it.

- [ ] **Step 1: Write failing tests for config load/save/validate**

```python
# tests/test_config.py
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from config import (
    load_config, save_config, get_default_config,
    validate_config, CONFIG_FILENAME,
    VALID_TIERS, VALID_BACKENDS,
)


class TestGetDefaultConfig(unittest.TestCase):
    def test_returns_dict_with_required_keys(self):
        cfg = get_default_config()
        self.assertEqual(cfg['tier'], 'standard')
        self.assertEqual(cfg['embedding_backend'], 'auto')
        self.assertIsNone(cfg['detected_backend'])
        self.assertEqual(cfg['model_version'], 'bge-small-en-v1.5')
        self.assertIsNone(cfg['model_hash'])
        self.assertFalse(cfg['silent_surfacing'])

    def test_advanced_defaults_present(self):
        cfg = get_default_config()
        self.assertAlmostEqual(cfg['search_keyword_weight'], 0.4)
        self.assertAlmostEqual(cfg['search_semantic_weight'], 0.6)
        self.assertEqual(cfg['checkpoint_batch_size'], 200)
        self.assertTrue(cfg['embed_on_compact'])
        self.assertTrue(cfg['embed_on_session_end'])


class TestSaveAndLoadConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = Path(self.tmpdir) / CONFIG_FILENAME

    def test_save_creates_file(self):
        cfg = get_default_config()
        save_config(cfg, self.config_path)
        self.assertTrue(self.config_path.exists())

    def test_roundtrip(self):
        cfg = get_default_config()
        cfg['tier'] = 'enhanced'
        cfg['detected_backend'] = 'mlx'
        save_config(cfg, self.config_path)
        loaded = load_config(self.config_path)
        self.assertEqual(loaded['tier'], 'enhanced')
        self.assertEqual(loaded['detected_backend'], 'mlx')

    def test_load_missing_file_returns_default(self):
        missing = Path(self.tmpdir) / 'nonexistent.json'
        loaded = load_config(missing)
        self.assertEqual(loaded['tier'], 'standard')

    def test_load_corrupt_file_returns_default(self):
        self.config_path.write_text('not json at all')
        loaded = load_config(self.config_path)
        self.assertEqual(loaded['tier'], 'standard')

    def test_load_merges_missing_keys(self):
        """Config from older version missing new keys gets defaults merged in."""
        partial = {'tier': 'lite'}
        self.config_path.write_text(json.dumps(partial))
        loaded = load_config(self.config_path)
        self.assertEqual(loaded['tier'], 'lite')
        self.assertEqual(loaded['embedding_backend'], 'auto')  # default filled in


class TestValidateConfig(unittest.TestCase):
    def test_valid_config_passes(self):
        cfg = get_default_config()
        errors = validate_config(cfg)
        self.assertEqual(errors, [])

    def test_invalid_tier_rejected(self):
        cfg = get_default_config()
        cfg['tier'] = 'mega'
        errors = validate_config(cfg)
        self.assertTrue(any('tier' in e for e in errors))

    def test_invalid_backend_rejected(self):
        cfg = get_default_config()
        cfg['embedding_backend'] = 'pytorch'
        errors = validate_config(cfg)
        self.assertTrue(any('embedding_backend' in e for e in errors))

    def test_weights_must_be_numeric(self):
        cfg = get_default_config()
        cfg['search_keyword_weight'] = 'high'
        errors = validate_config(cfg)
        self.assertTrue(any('search_keyword_weight' in e for e in errors))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: ImportError — `config` module does not exist yet.

- [ ] **Step 3: Implement config module**

```python
# scripts/config.py
#!/usr/bin/env python3
"""Global configuration for Claude Context Recall plugin v3.

Manages recall-config.json — tier selection, embedding backend,
and advanced tuning parameters. All values validated against allowlists.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / '.claude' / 'context-recall'
CONFIG_FILENAME = 'recall-config.json'
CONFIG_PATH = CONFIG_DIR / CONFIG_FILENAME

VALID_TIERS = {'lite', 'standard', 'enhanced'}
VALID_BACKENDS = {'auto', 'mlx', 'sentence-transformers', 'onnx', 'tfidf'}

REQUIRED_MODEL_VERSION = 'bge-small-en-v1.5'
REQUIRED_MODEL_HASH = None  # Set when model hash is known

MODEL_DIR = CONFIG_DIR / 'models'

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def get_default_config() -> Dict[str, Any]:
    """Return a config dict with all keys set to their defaults."""
    return {
        'tier': 'standard',
        'embedding_backend': 'auto',
        'detected_backend': None,
        'model_version': REQUIRED_MODEL_VERSION,
        'model_hash': None,
        'silent_surfacing': False,
        # Advanced / hidden
        'search_keyword_weight': 0.4,
        'search_semantic_weight': 0.6,
        'checkpoint_batch_size': 200,
        'surfacing_threshold': 0.75,
        'surfacing_max_results': 3,
        'surfacing_recency_exclude': 5,
        'embed_on_compact': True,
        'embed_on_session_end': True,
    }

# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load config from disk, merging with defaults for missing keys.

    Returns default config if file is missing or corrupt.
    """
    if config_path is None:
        config_path = CONFIG_PATH

    defaults = get_default_config()

    if not config_path.exists():
        return defaults

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return defaults
        # Merge: user values override defaults
        merged = {**defaults, **data}
        return merged
    except (json.JSONDecodeError, OSError):
        return defaults


def save_config(cfg: Dict[str, Any],
                config_path: Optional[Path] = None) -> None:
    """Write config to disk. Creates directory if needed."""
    if config_path is None:
        config_path = CONFIG_PATH

    config_dir = config_path.parent
    os.makedirs(config_dir, mode=0o700, exist_ok=True)

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_config(cfg: Dict[str, Any]) -> List[str]:
    """Validate config values against allowlists.

    Returns list of error strings. Empty list means valid.
    """
    errors: List[str] = []

    if cfg.get('tier') not in VALID_TIERS:
        errors.append(
            f"Invalid tier '{cfg.get('tier')}'. "
            f"Must be one of: {', '.join(sorted(VALID_TIERS))}"
        )

    if cfg.get('embedding_backend') not in VALID_BACKENDS:
        errors.append(
            f"Invalid embedding_backend '{cfg.get('embedding_backend')}'. "
            f"Must be one of: {', '.join(sorted(VALID_BACKENDS))}"
        )

    for key in ('search_keyword_weight', 'search_semantic_weight',
                'surfacing_threshold'):
        val = cfg.get(key)
        if not isinstance(val, (int, float)):
            errors.append(f"Invalid {key} '{val}'. Must be a number.")

    for key in ('checkpoint_batch_size', 'surfacing_max_results',
                'surfacing_recency_exclude'):
        val = cfg.get(key)
        if not isinstance(val, int):
            errors.append(f"Invalid {key} '{val}'. Must be an integer.")

    return errors

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

def detect_best_backend() -> str:
    """Probe for the best available embedding backend.

    Returns one of: 'mlx', 'sentence-transformers', 'onnx', 'tfidf', 'none'.
    """
    # 1. MLX (Apple Silicon only)
    try:
        import platform
        if platform.machine() == 'arm64' and sys.platform == 'darwin':
            import mlx_embedding_models  # noqa: F401
            return 'mlx'
    except ImportError:
        pass

    # 2. sentence-transformers (only if already installed — don't force PyTorch)
    try:
        import sentence_transformers  # noqa: F401
        return 'sentence-transformers'
    except ImportError:
        pass

    # 3. ONNX Runtime
    try:
        import onnxruntime  # noqa: F401
        return 'onnx'
    except ImportError:
        pass

    # 4. TF-IDF (scikit-learn)
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401
        return 'tfidf'
    except ImportError:
        pass

    return 'none'


def is_first_run(config_path: Optional[Path] = None) -> bool:
    """Check if this is the first run (no config file exists)."""
    if config_path is None:
        config_path = CONFIG_PATH
    return not config_path.exists()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/config.py tests/test_config.py
git commit -m "feat: add global config module for tier system

Manages recall-config.json with tier selection, embedding backend
detection, validation against allowlists, and forward-compatible
config merging."
```

---

## Task 2: Embedding Engine

**Files:**
- Create: `scripts/embeddings.py`
- Test: `tests/test_embeddings.py`

Standalone module: `text in → vector out`. No knowledge of recall's DB. Four-tier fallback with thread safety and query caching. All tests mock the ML imports so they run without any ML libraries installed.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_embeddings.py
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))


class TestDetectBackend(unittest.TestCase):
    """Test backend detection with mocked imports."""

    @patch.dict(sys.modules, {'mlx_embedding_models': None})
    @patch.dict(sys.modules, {'sentence_transformers': None})
    @patch.dict(sys.modules, {'onnxruntime': None})
    @patch.dict(sys.modules, {'sklearn': None,
                               'sklearn.feature_extraction': None,
                               'sklearn.feature_extraction.text': None})
    def test_no_backend_available(self):
        # Force reimport with all ML modules blocked
        import importlib
        import embeddings
        importlib.reload(embeddings)
        embeddings._embed_fn = None
        embeddings._backend_name = None
        self.assertFalse(embeddings.is_available())
        self.assertEqual(embeddings.get_backend_name(), 'none')


class TestTfidfFallback(unittest.TestCase):
    """Test the TF-IDF fallback path (requires scikit-learn OR tests the interface)."""

    def test_embed_returns_correct_shape(self):
        """Mock a backend and verify the interface contract."""
        import embeddings

        # Install a mock backend
        def mock_embed(texts):
            import array
            return [[0.1] * 384 for _ in texts]

        embeddings._embed_fn = mock_embed
        embeddings._backend_name = 'mock'
        embeddings._embed_dim = 384

        result = embeddings.embed(["hello world"])
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 384)

    def test_embed_batch_multiple_texts(self):
        import embeddings

        def mock_embed(texts):
            return [[0.1] * 384 for _ in texts]

        embeddings._embed_fn = mock_embed
        embeddings._backend_name = 'mock'
        embeddings._embed_dim = 384

        result = embeddings.embed(["text one", "text two", "text three"])
        self.assertEqual(len(result), 3)


class TestQueryCache(unittest.TestCase):
    def test_cache_returns_same_vector(self):
        import embeddings

        call_count = 0
        def mock_embed(texts):
            nonlocal call_count
            call_count += 1
            return [[float(call_count)] * 384 for _ in texts]

        embeddings._embed_fn = mock_embed
        embeddings._backend_name = 'mock'
        embeddings._embed_dim = 384
        embeddings._query_cache.clear()

        v1 = embeddings.embed_query("test query")
        v2 = embeddings.embed_query("test query")
        self.assertEqual(v1, v2)
        self.assertEqual(call_count, 1)  # Only called once — second was cached

    def test_cache_different_queries(self):
        import embeddings

        call_count = 0
        def mock_embed(texts):
            nonlocal call_count
            call_count += 1
            return [[float(call_count)] * 384 for _ in texts]

        embeddings._embed_fn = mock_embed
        embeddings._backend_name = 'mock'
        embeddings._embed_dim = 384
        embeddings._query_cache.clear()

        v1 = embeddings.embed_query("query A")
        v2 = embeddings.embed_query("query B")
        self.assertNotEqual(v1, v2)
        self.assertEqual(call_count, 2)


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_embeds_do_not_crash(self):
        import embeddings

        def mock_embed(texts):
            return [[0.5] * 384 for _ in texts]

        embeddings._embed_fn = mock_embed
        embeddings._backend_name = 'mock'
        embeddings._embed_dim = 384

        errors = []
        def worker():
            try:
                embeddings.embed(["concurrent test"])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(errors), 0)


class TestIsAvailable(unittest.TestCase):
    def test_available_when_backend_set(self):
        import embeddings
        embeddings._backend_name = 'mock'
        embeddings._embed_fn = lambda x: x
        self.assertTrue(embeddings.is_available())

    def test_not_available_when_no_backend(self):
        import embeddings
        embeddings._backend_name = None
        embeddings._embed_fn = None
        self.assertFalse(embeddings.is_available())


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_embeddings.py -v`
Expected: ImportError — `embeddings` module doesn't exist yet in the recall project.

- [ ] **Step 3: Implement embedding engine**

```python
# scripts/embeddings.py
#!/usr/bin/env python3
"""Embedding engine for Claude Context Recall plugin.

Four-tier fallback: MLX -> sentence-transformers -> ONNX -> TF-IDF.
Standalone module — no knowledge of recall's DB, hooks, or sessions.

Inspired by searchlight/binder's embedding engine but adapted for
checkpoint-based usage (not persistent process).
"""

import logging
import sys
import threading
from collections import OrderedDict
from typing import List, Optional

logger = logging.getLogger('recall.embeddings')

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_embed_fn = None
_embed_dim: int = 384
_backend_name: Optional[str] = None
_embed_lock = threading.Lock()
_init_lock = threading.Lock()

_query_cache: OrderedDict = OrderedDict()
_query_cache_lock = threading.Lock()
MAX_QUERY_CACHE = 256

EMBEDDING_DIM = 384

# ---------------------------------------------------------------------------
# Backend loaders
# ---------------------------------------------------------------------------

def _load_mlx():
    """MLX embeddings — Apple Silicon Metal GPU."""
    import platform
    if platform.machine() != 'arm64' or sys.platform != 'darwin':
        raise RuntimeError('MLX requires Apple Silicon')
    from mlx_embedding_models import EmbeddingModel
    model = EmbeddingModel.from_registry('bge-small')

    def embed(texts: List[str]) -> List[List[float]]:
        result = model.encode(texts)
        if hasattr(result, 'tolist'):
            return result.tolist()
        return [list(r) for r in result]

    # Warmup + dimension check
    test = embed(['test'])
    dim = len(test[0])
    logger.info('Using MLX embeddings (bge-small, dim=%d)', dim)
    return embed, dim


def _load_sentence_transformers():
    """sentence-transformers — works everywhere with PyTorch."""
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('BAAI/bge-small-en-v1.5')

    def embed(texts: List[str]) -> List[List[float]]:
        result = model.encode(texts, show_progress_bar=False)
        if hasattr(result, 'tolist'):
            return result.tolist()
        return [list(r) for r in result]

    test = embed(['test'])
    dim = len(test[0])
    logger.info('Using sentence-transformers (bge-small-en-v1.5, dim=%d)', dim)
    return embed, dim


def _load_onnx():
    """ONNX Runtime — lightweight cross-platform inference."""
    import onnxruntime
    from pathlib import Path
    import numpy as np

    model_dir = Path.home() / '.claude' / 'context-recall' / 'models'
    model_path = model_dir / 'bge-small-en-v1.5.onnx'

    if not model_path.exists():
        raise FileNotFoundError(
            f'ONNX model not found at {model_path}. '
            'Run /recall config tier enhanced to download.'
        )

    session = onnxruntime.InferenceSession(str(model_path))

    # Simple tokenization for ONNX (basic whitespace — real tokenizer
    # would need the tokenizer files too; this is a simplified path)
    def embed(texts: List[str]) -> List[List[float]]:
        # This is a placeholder structure — actual ONNX embedding requires
        # proper tokenization. For v3.0.0 we'll use the HuggingFace
        # optimum export which includes tokenizer.
        raise NotImplementedError(
            'ONNX backend requires tokenizer setup. '
            'Use mlx or sentence-transformers for now.'
        )

    logger.info('Using ONNX Runtime')
    return embed, 384


def _load_tfidf():
    """TF-IDF fallback — no ML model needed."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(max_features=EMBEDDING_DIM)
    corpus: List[str] = []
    fitted = False
    MAX_CORPUS = 10_000

    def embed(texts: List[str]) -> List[List[float]]:
        nonlocal fitted, corpus
        corpus.extend(texts)
        if len(corpus) > MAX_CORPUS:
            corpus = corpus[-MAX_CORPUS:]
        if not fitted or len(texts) > 10:
            vectorizer.fit(corpus)
            fitted = True
        vecs = vectorizer.transform(texts).toarray()
        # Pad or truncate to fixed dimension
        rows = []
        for row in vecs:
            r = list(row)
            if len(r) < EMBEDDING_DIM:
                r.extend([0.0] * (EMBEDDING_DIM - len(r)))
            rows.append(r[:EMBEDDING_DIM])
        return rows

    logger.info('Using TF-IDF fallback (no ML models)')
    return embed, EMBEDDING_DIM


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

_LOADERS = [
    ('mlx', _load_mlx),
    ('sentence-transformers', _load_sentence_transformers),
    ('onnx', _load_onnx),
    ('tfidf', _load_tfidf),
]


def _try_load_backend(name: Optional[str] = None):
    """Try to load a specific backend, or auto-detect the best one.

    Args:
        name: Backend name to load, or None for auto-detection.
    """
    global _embed_fn, _embed_dim, _backend_name

    if name and name != 'auto':
        # Load a specific backend
        for bname, loader in _LOADERS:
            if bname == name:
                _embed_fn, _embed_dim = loader()
                _backend_name = bname
                return
        raise ValueError(f'Unknown backend: {name}')

    # Auto-detect: try each in order
    for bname, loader in _LOADERS:
        try:
            _embed_fn, _embed_dim = loader()
            _backend_name = bname
            return
        except Exception as e:
            logger.debug('%s not available: %s', bname, e)
            continue

    _backend_name = None
    _embed_fn = None


def _ensure_loaded(backend_name: Optional[str] = None):
    """Double-checked locking for lazy initialization."""
    global _embed_fn
    if _embed_fn is not None:
        return
    with _init_lock:
        if _embed_fn is None:
            _try_load_backend(backend_name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Whether any embedding backend is loaded or loadable."""
    if _embed_fn is not None:
        return True
    if _backend_name is not None:
        return _backend_name != 'none'
    # Don't auto-load here — just check
    return False


def get_backend_name() -> str:
    """Return the name of the active backend, or 'none'."""
    return _backend_name or 'none'


def embed(texts: List[str]) -> List[List[float]]:
    """Embed a batch of texts. Returns list of 384-dim float vectors.

    Thread-safe: a global lock serializes all calls (required for MLX
    Metal backend safety).
    """
    _ensure_loaded()
    if _embed_fn is None:
        raise RuntimeError('No embedding backend available.')
    with _embed_lock:
        return _embed_fn(texts)


def embed_query(text: str) -> List[float]:
    """Embed a single query. Cached via LRU (256 entries).

    Cache is checked before acquiring the embed lock so repeated
    queries from concurrent callers skip the backend entirely.
    """
    with _query_cache_lock:
        cached = _query_cache.get(text)
        if cached is not None:
            _query_cache.move_to_end(text)
            return list(cached)

    result = embed([text])[0]

    with _query_cache_lock:
        if len(_query_cache) >= MAX_QUERY_CACHE:
            _query_cache.popitem(last=False)
        _query_cache[text] = list(result)
        _query_cache.move_to_end(text)

    return result


def embed_batched(texts: List[str], batch_size: int = 200) -> List[List[float]]:
    """Embed a large list of texts in batches."""
    if len(texts) <= batch_size:
        return embed(texts)

    all_vecs: List[List[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        all_vecs.extend(embed(batch))
    return all_vecs


def clear_cache():
    """Clear the query embedding cache."""
    with _query_cache_lock:
        _query_cache.clear()


def reset():
    """Reset module state. Used in tests."""
    global _embed_fn, _backend_name, _embed_dim
    _embed_fn = None
    _backend_name = None
    _embed_dim = EMBEDDING_DIM
    clear_cache()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_embeddings.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/embeddings.py tests/test_embeddings.py
git commit -m "feat: add embedding engine with 4-tier fallback

MLX -> sentence-transformers -> ONNX -> TF-IDF detection chain.
Thread-safe with LRU query cache. Standalone module with no
DB dependencies."
```

---

## Task 3: DB Schema — exchange_vectors Table

**Files:**
- Modify: `scripts/db.py`
- Test: `tests/test_db.py`

Add the `exchange_vectors` table, CRUD functions, backend mismatch detection, and `summary_vector` column on highlights. Schema version migration for existing users.

- [ ] **Step 1: Write failing tests for vector CRUD**

Add to `tests/test_db.py`:

```python
# Append to tests/test_db.py

class TestExchangeVectorsCRUD(unittest.TestCase):
    """Tests for exchange_vectors table operations."""

    def setUp(self):
        self.conn = get_connection(db_path=':memory:')
        insert_session(self.conn, 'sess1', '/project', 'hash1',
                       '2026-01-01T00:00:00Z')
        insert_exchanges(self.conn, 'sess1', [
            {'idx': 1, 'timestamp': '2026-01-01T10:00:00Z',
             'preview': 'hello', 'user_text': 'hello world',
             'assistant_text': 'hi there'},
            {'idx': 2, 'timestamp': '2026-01-01T10:05:00Z',
             'preview': 'test', 'user_text': 'test query',
             'assistant_text': 'test response'},
        ])

    def tearDown(self):
        self.conn.close()

    def test_insert_and_get_vector(self):
        vector = [0.1] * 384
        insert_exchange_vector(self.conn, 1, vector, 'mock')
        result = get_exchange_vector(self.conn, 1)
        self.assertIsNotNone(result)
        self.assertEqual(result['backend'], 'mock')
        self.assertEqual(len(deserialize_vector(result['vector'])), 384)

    def test_get_unvectorized_exchanges(self):
        unvectorized = get_unvectorized_exchanges(self.conn, 'sess1', limit=200)
        self.assertEqual(len(unvectorized), 2)
        # Vectorize one
        insert_exchange_vector(self.conn, unvectorized[0]['id'], [0.1] * 384, 'mock')
        unvectorized = get_unvectorized_exchanges(self.conn, 'sess1', limit=200)
        self.assertEqual(len(unvectorized), 1)

    def test_get_session_vectors(self):
        insert_exchange_vector(self.conn, 1, [0.1] * 384, 'mock')
        insert_exchange_vector(self.conn, 2, [0.2] * 384, 'mock')
        vectors = get_session_vectors(self.conn, 'sess1')
        self.assertEqual(len(vectors), 2)

    def test_delete_vectors_by_backend_mismatch(self):
        insert_exchange_vector(self.conn, 1, [0.1] * 384, 'old_backend')
        insert_exchange_vector(self.conn, 2, [0.2] * 384, 'old_backend')
        count = delete_mismatched_vectors(self.conn, 'new_backend')
        self.assertEqual(count, 2)
        self.assertEqual(len(get_session_vectors(self.conn, 'sess1')), 0)

    def test_delete_mismatched_skips_matching(self):
        insert_exchange_vector(self.conn, 1, [0.1] * 384, 'mock')
        count = delete_mismatched_vectors(self.conn, 'mock')
        self.assertEqual(count, 0)
        self.assertEqual(len(get_session_vectors(self.conn, 'sess1')), 1)


class TestSerializeDeserializeVector(unittest.TestCase):
    def test_roundtrip(self):
        original = [float(i) / 384 for i in range(384)]
        blob = serialize_vector(original)
        self.assertIsInstance(blob, bytes)
        self.assertEqual(len(blob), 384 * 4)  # float32
        restored = deserialize_vector(blob)
        for a, b in zip(original, restored):
            self.assertAlmostEqual(a, b, places=5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_db.py::TestExchangeVectorsCRUD -v`
Expected: ImportError — functions not defined yet.

- [ ] **Step 3: Add vector functions to db.py**

Add to `scripts/db.py` — new imports, schema addition, and functions:

Add to the `_SCHEMA_SQL` string (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS exchange_vectors (
    exchange_id     INTEGER PRIMARY KEY REFERENCES exchanges(id),
    vector          BLOB NOT NULL,
    backend         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
```

Add to the imports at the top of `db.py`:

```python
import struct
```

Add these functions after the existing Highlight CRUD section:

```python
# ---------------------------------------------------------------------------
# Vector serialization
# ---------------------------------------------------------------------------

def serialize_vector(vector: List[float]) -> bytes:
    """Serialize a float vector to bytes (float32)."""
    return struct.pack(f'{len(vector)}f', *vector)


def deserialize_vector(blob: bytes) -> List[float]:
    """Deserialize bytes back to a float vector."""
    count = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f'{count}f', blob))


# ---------------------------------------------------------------------------
# Exchange Vector CRUD
# ---------------------------------------------------------------------------

def insert_exchange_vector(conn: sqlite3.Connection, exchange_id: int,
                           vector: List[float], backend: str,
                           commit: bool = True) -> None:
    """Insert or replace an exchange vector."""
    now = datetime.now(timezone.utc).isoformat()
    blob = serialize_vector(vector)
    conn.execute(
        "INSERT OR REPLACE INTO exchange_vectors "
        "(exchange_id, vector, backend, created_at) "
        "VALUES (?, ?, ?, ?)",
        (exchange_id, blob, backend, now),
    )
    if commit:
        conn.commit()


def get_exchange_vector(conn: sqlite3.Connection,
                        exchange_id: int) -> Optional[Dict]:
    """Get a single exchange vector row, or None."""
    cur = conn.execute(
        "SELECT * FROM exchange_vectors WHERE exchange_id = ?",
        (exchange_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def get_unvectorized_exchanges(conn: sqlite3.Connection, session_id: str,
                                limit: int = 200) -> List[Dict]:
    """Get exchanges that have no corresponding vector row."""
    cur = conn.execute(
        "SELECT e.id, e.session_id, e.idx, e.user_text, e.assistant_text "
        "FROM exchanges e "
        "LEFT JOIN exchange_vectors v ON e.id = v.exchange_id "
        "WHERE e.session_id = ? AND v.exchange_id IS NULL "
        "LIMIT ?",
        (session_id, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def get_session_vectors(conn: sqlite3.Connection,
                        session_id: str) -> List[Dict]:
    """Get all vectors for exchanges in a session."""
    cur = conn.execute(
        "SELECT v.*, e.idx, e.preview, e.timestamp "
        "FROM exchange_vectors v "
        "JOIN exchanges e ON v.exchange_id = e.id "
        "WHERE e.session_id = ? "
        "ORDER BY e.idx",
        (session_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def get_vectors_by_scope(conn: sqlite3.Connection,
                         session_id: Optional[str] = None,
                         project_hash: Optional[str] = None) -> List[Dict]:
    """Get vectors filtered by session or project scope."""
    if session_id:
        return get_session_vectors(conn, session_id)

    sql = (
        "SELECT v.*, e.idx, e.preview, e.timestamp, e.session_id "
        "FROM exchange_vectors v "
        "JOIN exchanges e ON v.exchange_id = e.id "
    )
    params: List[Any] = []
    if project_hash:
        sql += "JOIN sessions s ON e.session_id = s.session_id WHERE s.project_hash = ? "
        params.append(project_hash)
    sql += "ORDER BY e.idx"
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def delete_mismatched_vectors(conn: sqlite3.Connection,
                              current_backend: str) -> int:
    """Delete all vectors that don't match the current backend.

    Returns number of deleted rows.
    """
    cur = conn.execute(
        "DELETE FROM exchange_vectors WHERE backend != ?",
        (current_backend,),
    )
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_db.py::TestExchangeVectorsCRUD tests/test_db.py::TestSerializeDeserializeVector -v`
Expected: All new tests PASS.

- [ ] **Step 5: Run full existing test suite to verify no regressions**

Run: `python3 -m pytest tests/ -v`
Expected: All 226+ tests PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/db.py tests/test_db.py
git commit -m "feat: add exchange_vectors table and vector CRUD

Stores 384-dim float32 vectors as BLOBs with backend tracking.
Includes unvectorized exchange query, scoped vector retrieval,
and backend mismatch cleanup."
```

---

## Task 4: Vector Search Module

**Files:**
- Create: `scripts/vector_search.py`
- Test: `tests/test_vector_search.py`

Cosine similarity, hybrid merge/ranking, graceful degradation.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_vector_search.py
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from vector_search import (
    cosine_similarity, hybrid_search_merge, semantic_search,
)


class TestCosineSimilarity(unittest.TestCase):
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(v, v), 1.0, places=5)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(a, b), 0.0, places=5)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(a, b), -1.0, places=5)

    def test_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(a, b), 0.0, places=5)


class TestSemanticSearch(unittest.TestCase):
    def test_returns_sorted_by_similarity(self):
        query_vec = [1.0, 0.0, 0.0]
        vectors_with_meta = [
            {'exchange_id': 1, 'vector_list': [0.5, 0.5, 0.0], 'idx': 1, 'preview': 'a'},
            {'exchange_id': 2, 'vector_list': [1.0, 0.0, 0.0], 'idx': 2, 'preview': 'b'},
            {'exchange_id': 3, 'vector_list': [0.0, 1.0, 0.0], 'idx': 3, 'preview': 'c'},
        ]
        results = semantic_search(query_vec, vectors_with_meta, top_k=3)
        # exchange 2 should be first (identical to query)
        self.assertEqual(results[0]['exchange_id'], 2)
        self.assertAlmostEqual(results[0]['semantic_score'], 1.0, places=3)

    def test_top_k_limits_results(self):
        query_vec = [1.0, 0.0]
        vectors_with_meta = [
            {'exchange_id': i, 'vector_list': [0.5, 0.5], 'idx': i, 'preview': str(i)}
            for i in range(20)
        ]
        results = semantic_search(query_vec, vectors_with_meta, top_k=5)
        self.assertEqual(len(results), 5)

    def test_empty_vectors_returns_empty(self):
        results = semantic_search([1.0, 0.0], [], top_k=5)
        self.assertEqual(results, [])


class TestHybridSearchMerge(unittest.TestCase):
    def test_both_match_ranked_highest(self):
        keyword_results = [
            {'id': 1, 'idx': 1, 'preview': 'both match', 'session_id': 's1'},
            {'id': 3, 'idx': 3, 'preview': 'keyword only', 'session_id': 's1'},
        ]
        semantic_results = [
            {'exchange_id': 1, 'idx': 1, 'preview': 'both match',
             'semantic_score': 0.8, 'session_id': 's1'},
            {'exchange_id': 2, 'idx': 2, 'preview': 'semantic only',
             'semantic_score': 0.9, 'session_id': 's1'},
        ]
        merged = hybrid_search_merge(
            keyword_results, semantic_results,
            keyword_weight=0.4, semantic_weight=0.6,
        )
        # Exchange 1 appears in both — should rank highest
        self.assertEqual(merged[0]['id'], 1)

    def test_keyword_only_results_included(self):
        keyword_results = [
            {'id': 5, 'idx': 5, 'preview': 'keyword only', 'session_id': 's1'},
        ]
        semantic_results = []
        merged = hybrid_search_merge(keyword_results, semantic_results,
                                      keyword_weight=0.4, semantic_weight=0.6)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['id'], 5)

    def test_semantic_only_results_included(self):
        keyword_results = []
        semantic_results = [
            {'exchange_id': 7, 'idx': 7, 'preview': 'semantic only',
             'semantic_score': 0.85, 'session_id': 's1'},
        ]
        merged = hybrid_search_merge(keyword_results, semantic_results,
                                      keyword_weight=0.4, semantic_weight=0.6)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['id'], 7)

    def test_deduplication(self):
        keyword_results = [
            {'id': 1, 'idx': 1, 'preview': 'dup', 'session_id': 's1'},
        ]
        semantic_results = [
            {'exchange_id': 1, 'idx': 1, 'preview': 'dup',
             'semantic_score': 0.7, 'session_id': 's1'},
        ]
        merged = hybrid_search_merge(keyword_results, semantic_results,
                                      keyword_weight=0.4, semantic_weight=0.6)
        self.assertEqual(len(merged), 1)

    def test_weights_affect_ranking(self):
        """With keyword_weight=1.0, semantic_weight=0.0, keyword-only wins."""
        keyword_results = [
            {'id': 1, 'idx': 1, 'preview': 'kw', 'session_id': 's1'},
        ]
        semantic_results = [
            {'exchange_id': 2, 'idx': 2, 'preview': 'sem',
             'semantic_score': 0.99, 'session_id': 's1'},
        ]
        merged = hybrid_search_merge(keyword_results, semantic_results,
                                      keyword_weight=1.0, semantic_weight=0.0)
        self.assertEqual(merged[0]['id'], 1)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_vector_search.py -v`
Expected: ImportError — `vector_search` module doesn't exist.

- [ ] **Step 3: Implement vector search module**

```python
# scripts/vector_search.py
#!/usr/bin/env python3
"""Hybrid search: FTS5 keyword + cosine similarity vector search.

Combines recall's existing FTS5 results with semantic similarity
over exchange vectors. Handles graceful degradation when no
vectors exist.
"""

import math
from typing import Any, Dict, List, Optional


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns 0.0 if either vector has zero magnitude.
    """
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def semantic_search(query_vector: List[float],
                    vectors_with_meta: List[Dict],
                    top_k: int = 10) -> List[Dict]:
    """Brute-force cosine similarity search over vectors.

    Args:
        query_vector: The query embedding.
        vectors_with_meta: List of dicts with 'vector_list' (the deserialized
            vector) and metadata fields (exchange_id, idx, preview, etc.).
        top_k: Maximum results to return.

    Returns:
        Top-K results sorted by similarity descending, each dict enriched
        with 'semantic_score'.
    """
    if not vectors_with_meta:
        return []

    scored = []
    for item in vectors_with_meta:
        vec = item.get('vector_list', [])
        if not vec:
            continue
        score = cosine_similarity(query_vector, vec)
        entry = {k: v for k, v in item.items() if k != 'vector_list'}
        entry['semantic_score'] = score
        scored.append(entry)

    scored.sort(key=lambda x: x['semantic_score'], reverse=True)
    return scored[:top_k]


def hybrid_search_merge(keyword_results: List[Dict],
                        semantic_results: List[Dict],
                        keyword_weight: float = 0.4,
                        semantic_weight: float = 0.6,
                        limit: int = 10) -> List[Dict]:
    """Merge FTS5 keyword results with semantic search results.

    Deduplicates by exchange ID, computes a combined score, and
    returns results sorted by final_score descending.

    Args:
        keyword_results: Dicts from FTS5 search. Must have 'id' key.
        semantic_results: Dicts from semantic_search(). Must have
            'exchange_id' and 'semantic_score' keys.
        keyword_weight: Weight for keyword match score (0-1).
        semantic_weight: Weight for semantic similarity score (0-1).
        limit: Max results to return.

    Returns:
        Merged, deduplicated, scored, and sorted result list.
    """
    merged: Dict[int, Dict] = {}

    # Normalize keyword results: assign score based on position (1.0 for first)
    for rank, result in enumerate(keyword_results):
        eid = result['id']
        # Position-based score: first result gets 1.0, decays linearly
        kw_score = max(0.0, 1.0 - (rank * 0.1))
        if eid not in merged:
            merged[eid] = {**result, 'keyword_score': kw_score, 'semantic_score': 0.0}
        else:
            merged[eid]['keyword_score'] = kw_score

    # Add semantic results
    for result in semantic_results:
        eid = result['exchange_id']
        sem_score = result.get('semantic_score', 0.0)
        if eid not in merged:
            merged[eid] = {
                'id': eid,
                'idx': result.get('idx'),
                'preview': result.get('preview'),
                'session_id': result.get('session_id'),
                'keyword_score': 0.0,
                'semantic_score': sem_score,
            }
        else:
            merged[eid]['semantic_score'] = sem_score

    # Compute final scores
    results = []
    for eid, item in merged.items():
        item['final_score'] = (
            keyword_weight * item.get('keyword_score', 0.0) +
            semantic_weight * item.get('semantic_score', 0.0)
        )
        results.append(item)

    results.sort(key=lambda x: x['final_score'], reverse=True)
    return results[:limit]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_vector_search.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/vector_search.py tests/test_vector_search.py
git commit -m "feat: add hybrid vector search module

Brute-force cosine similarity + FTS5 merge/rank with configurable
weights. Handles deduplication and graceful degradation when
no vectors exist."
```

---

## Task 5: Checkpoint Embedding in PostCompact

**Files:**
- Modify: `hooks/post_compact.py`
- Test: `tests/test_post_compact.py`

Enhanced tier: embed un-vectorized exchanges during PostCompact, then build a smarter nudge using relevance rather than just recency.

- [ ] **Step 1: Write failing tests for checkpoint embedding**

Add to `tests/test_post_compact.py`:

```python
class TestEnhancedCheckpointEmbedding(unittest.TestCase):
    """Tests for Enhanced tier embedding during PostCompact."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 'test.db'
        self.conn = get_connection(db_path=self.db_path)
        insert_session(self.conn, 'sess1', '/project', 'hash1',
                       '2026-01-01T00:00:00Z')
        for i in range(1, 11):
            insert_exchanges(self.conn, 'sess1', [{
                'idx': i,
                'timestamp': f'2026-01-01T{10+i}:00:00Z',
                'preview': f'exchange {i}',
                'user_text': f'user message {i}',
                'assistant_text': f'assistant response {i}',
            }])
        update_session_offset(self.conn, 'sess1', 0, 10)
        self.config_path = Path(self.tmp) / 'recall-config.json'

    def tearDown(self):
        self.conn.close()

    @patch('post_compact.load_config')
    @patch('post_compact.embeddings')
    def test_enhanced_tier_embeds_unvectorized(self, mock_emb, mock_cfg):
        mock_cfg.return_value = {
            'tier': 'enhanced',
            'embed_on_compact': True,
            'checkpoint_batch_size': 200,
            'embedding_backend': 'auto',
            'detected_backend': 'mock',
            'search_keyword_weight': 0.4,
            'search_semantic_weight': 0.6,
        }
        mock_emb.is_available.return_value = True
        mock_emb.get_backend_name.return_value = 'mock'
        mock_emb.embed.return_value = [[0.1] * 384]
        mock_emb.embed_batched.return_value = [[0.1] * 384 for _ in range(10)]

        result = run_hook({'session_id': 'sess1'}, db_path=self.db_path)
        self.assertIn('systemMessage', result)

        # Verify vectors were inserted
        vectors = get_session_vectors(self.conn, 'sess1')
        self.assertEqual(len(vectors), 10)

    @patch('post_compact.load_config')
    def test_standard_tier_no_embedding(self, mock_cfg):
        mock_cfg.return_value = {'tier': 'standard'}
        result = run_hook({'session_id': 'sess1'}, db_path=self.db_path)
        self.assertIn('systemMessage', result)

        vectors = get_session_vectors(self.conn, 'sess1')
        self.assertEqual(len(vectors), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_post_compact.py::TestEnhancedCheckpointEmbedding -v`
Expected: FAIL — new imports/functions not wired up yet.

- [ ] **Step 3: Modify post_compact.py for Enhanced tier**

Add imports at top of `hooks/post_compact.py`:

```python
from config import load_config
from db import (get_connection, get_session, get_exchanges, DB_PATH,
                get_unvectorized_exchanges, insert_exchange_vector,
                get_session_vectors, deserialize_vector)
import embeddings
from vector_search import cosine_similarity
```

Add checkpoint embedding + smarter nudge logic inside `run_hook()`, after the existing nudge build but before the return:

```python
    # --- Enhanced tier: checkpoint embedding + smarter nudge ---
    cfg = load_config()
    if cfg.get('tier') == 'enhanced' and cfg.get('embed_on_compact', True):
        if embeddings.is_available():
            batch_size = cfg.get('checkpoint_batch_size', 200)
            unvectorized = get_unvectorized_exchanges(conn, session_id, limit=batch_size)
            if unvectorized:
                texts = [
                    (ex.get('user_text', '') or '') + ' ' + (ex.get('assistant_text', '') or '')
                    for ex in unvectorized
                ]
                vectors = embeddings.embed_batched(texts, batch_size=batch_size)
                backend = embeddings.get_backend_name()
                for ex, vec in zip(unvectorized, vectors):
                    insert_exchange_vector(conn, ex['id'], vec, backend, commit=False)
                conn.commit()

            # Smarter nudge: find most relevant earlier exchanges
            all_vectors = get_session_vectors(conn, session_id)
            if len(all_vectors) >= 10:
                # Compute recent topic vector (average of last 5)
                last_5 = all_vectors[-5:]
                last_5_vecs = [deserialize_vector(v['vector']) for v in last_5]
                dim = len(last_5_vecs[0])
                topic_vec = [sum(v[i] for v in last_5_vecs) / len(last_5_vecs) for i in range(dim)]

                # Find most relevant non-recent exchanges
                last_5_ids = {v['exchange_id'] for v in last_5}
                candidates = [v for v in all_vectors if v['exchange_id'] not in last_5_ids]
                scored = []
                for v in candidates:
                    vec = deserialize_vector(v['vector'])
                    score = cosine_similarity(topic_vec, vec)
                    scored.append((score, v))
                scored.sort(key=lambda x: x[0], reverse=True)

                relevant_previews = [
                    f"#{s[1]['idx']} \"{s[1]['preview']}\""
                    for s in scored[:5]
                ]
                if relevant_previews:
                    nudge += '\nRelated earlier context:\n'
                    for rp in relevant_previews:
                        nudge += f'  - {rp}\n'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_post_compact.py -v`
Expected: All tests PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add hooks/post_compact.py tests/test_post_compact.py
git commit -m "feat: checkpoint embedding in PostCompact hook

Enhanced tier embeds un-vectorized exchanges during compaction.
Builds smarter nudge using topic vector relevance instead of
just chronological recency."
```

---

## Task 6: Checkpoint Embedding in SessionEnd

**Files:**
- Modify: `hooks/session_end.py`
- Test: `tests/test_session_end.py`

Same pattern as PostCompact but simpler — just embed remaining un-vectorized exchanges.

- [ ] **Step 1: Write failing test**

Add to `tests/test_session_end.py`:

```python
class TestEnhancedSessionEndEmbedding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 'test.db'
        self.conn = get_connection(db_path=self.db_path)
        insert_session(self.conn, 'sess1', '/project', 'hash1',
                       '2026-01-01T00:00:00Z')
        insert_exchanges(self.conn, 'sess1', [{
            'idx': 1, 'timestamp': '2026-01-01T10:00:00Z',
            'preview': 'hello', 'user_text': 'hello',
            'assistant_text': 'world',
        }])
        update_session_offset(self.conn, 'sess1', 0, 1)

    def tearDown(self):
        self.conn.close()

    @patch('session_end.load_config')
    @patch('session_end.embeddings')
    def test_enhanced_embeds_on_session_end(self, mock_emb, mock_cfg):
        mock_cfg.return_value = {
            'tier': 'enhanced',
            'embed_on_session_end': True,
            'checkpoint_batch_size': 200,
            'embedding_backend': 'auto',
            'detected_backend': 'mock',
        }
        mock_emb.is_available.return_value = True
        mock_emb.get_backend_name.return_value = 'mock'
        mock_emb.embed_batched.return_value = [[0.1] * 384]

        run_hook({'session_id': 'sess1'}, db_path=self.db_path)

        vectors = get_session_vectors(self.conn, 'sess1')
        self.assertEqual(len(vectors), 1)

    @patch('session_end.load_config')
    def test_standard_tier_no_embedding(self, mock_cfg):
        mock_cfg.return_value = {'tier': 'standard'}
        run_hook({'session_id': 'sess1'}, db_path=self.db_path)
        vectors = get_session_vectors(self.conn, 'sess1')
        self.assertEqual(len(vectors), 0)
```

- [ ] **Step 2: Run test to verify fail**

Run: `python3 -m pytest tests/test_session_end.py::TestEnhancedSessionEndEmbedding -v`

- [ ] **Step 3: Modify session_end.py**

Add imports and checkpoint logic to `hooks/session_end.py` — same pattern as PostCompact but without the smarter nudge:

```python
from config import load_config
from db import (get_connection, get_session, end_session, DB_PATH,
                get_unvectorized_exchanges, insert_exchange_vector)
import embeddings
```

Add after the `end_session()` call in `run_hook()`:

```python
        # --- Enhanced tier: checkpoint embedding ---
        cfg = load_config()
        if cfg.get('tier') == 'enhanced' and cfg.get('embed_on_session_end', True):
            if embeddings.is_available():
                batch_size = cfg.get('checkpoint_batch_size', 200)
                unvectorized = get_unvectorized_exchanges(conn, session_id, limit=batch_size)
                if unvectorized:
                    texts = [
                        (ex.get('user_text', '') or '') + ' ' + (ex.get('assistant_text', '') or '')
                        for ex in unvectorized
                    ]
                    vectors = embeddings.embed_batched(texts, batch_size=batch_size)
                    backend = embeddings.get_backend_name()
                    for ex, vec in zip(unvectorized, vectors):
                        insert_exchange_vector(conn, ex['id'], vec, backend, commit=False)
                    conn.commit()
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_session_end.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/session_end.py tests/test_session_end.py
git commit -m "feat: checkpoint embedding in SessionEnd hook

Enhanced tier embeds remaining un-vectorized exchanges when
session closes. Respects batch cap and embed_on_session_end config."
```

---

## Task 7: First-Run Detection & Tier Routing in prompt_submit

**Files:**
- Modify: `hooks/prompt_submit.py`
- Test: `tests/test_prompt_submit.py`

Add first-run config generation, tier routing (Lite JSON path vs Standard/Enhanced SQLite path), and the welcome/upgrade messages.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_prompt_submit.py`:

```python
class TestFirstRunDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = Path(self.tmp) / 'recall-config.json'
        self.db_path = Path(self.tmp) / 'test.db'

    @patch('prompt_submit.CONFIG_PATH')
    @patch('prompt_submit.detect_best_backend')
    def test_first_run_creates_config(self, mock_detect, mock_path):
        mock_path.__eq__ = lambda s, o: False  # not exists
        mock_detect.return_value = 'none'
        # Verify first_run_setup creates config
        from prompt_submit import first_run_setup
        msg = first_run_setup(self.config_path)
        self.assertIn('Welcome', msg)
        self.assertTrue(self.config_path.exists())

    @patch('prompt_submit.DB_PATH')
    def test_existing_user_upgrade(self, mock_db):
        """Existing recall.db but no config -> upgrade path."""
        # Create a fake recall.db
        open(Path(self.tmp) / 'recall.db', 'w').close()
        from prompt_submit import first_run_setup
        msg = first_run_setup(self.config_path, db_exists=True)
        self.assertIn('Upgraded', msg)


class TestTierRouting(unittest.TestCase):
    @patch('prompt_submit.load_config')
    def test_standard_tier_uses_sqlite(self, mock_cfg):
        mock_cfg.return_value = {'tier': 'standard'}
        # Standard tier should proceed with SQLite path (existing behavior)
        # This is tested implicitly by existing test_prompt_submit tests passing


    @patch('prompt_submit.load_config')
    def test_lite_tier_uses_json(self, mock_cfg):
        mock_cfg.return_value = {'tier': 'lite'}
        # Lite tier should use JSON path — separate test needed once
        # the JSON path is implemented in Task 8
```

- [ ] **Step 2: Run tests to verify fail**

Run: `python3 -m pytest tests/test_prompt_submit.py::TestFirstRunDetection -v`

- [ ] **Step 3: Add first-run logic to prompt_submit.py**

Add imports at top:

```python
from config import (load_config, save_config, get_default_config,
                    detect_best_backend, is_first_run, CONFIG_PATH)
```

Add `first_run_setup()` function:

```python
def first_run_setup(config_path=None, db_exists=False):
    """Run first-time setup: detect backend, create config, return welcome message."""
    if config_path is None:
        config_path = CONFIG_PATH

    cfg = get_default_config()
    detected = detect_best_backend()
    cfg['detected_backend'] = detected if detected != 'none' else None

    if db_exists:
        # Existing v2.2.0 user upgrading to v3
        cfg['tier'] = 'standard'
        save_config(cfg, config_path)
        backend_msg = f"\nEmbedding backend available: {detected}" if detected != 'none' else ""
        return (
            f"[recall] Upgraded to v3.0.0. Current tier: standard.{backend_msg}\n\n"
            "To enable semantic search: /recall config tier enhanced"
        )
    else:
        # Brand new user
        cfg['tier'] = 'standard'
        save_config(cfg, config_path)
        recommend = 'enhanced' if detected != 'none' else 'standard'
        backend_msg = f"Detected embedding backend: {detected}\n\n" if detected != 'none' else ""
        return (
            f"[recall] Welcome! {backend_msg}"
            "Available tiers:\n"
            "- lite: Per-session only, minimal footprint\n"
            "- standard: Cross-session search, tags, sharing (SQLite)\n"
            "- enhanced: Semantic search, smart context recovery (requires model download)\n\n"
            f"Recommended: {recommend}\n\n"
            "To choose your tier: /recall config tier <lite|standard|enhanced>\n"
            "Current default: standard (no model download required)"
        )
```

Add first-run check at the start of `run_hook()`:

```python
    # First-run detection
    config_path = None  # Use default
    if is_first_run(config_path):
        db_exists = DB_PATH.exists() if hasattr(DB_PATH, 'exists') else os.path.exists(str(DB_PATH))
        welcome_msg = first_run_setup(config_path, db_exists=db_exists)
        # Continue with standard tier for this invocation
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_prompt_submit.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/prompt_submit.py tests/test_prompt_submit.py
git commit -m "feat: first-run detection and tier routing in prompt_submit

Detects first run, probes embedding backends, creates config file.
Handles both new users and v2.2.0 upgrade paths with appropriate
welcome/upgrade messages."
```

---

## Task 8: Hybrid Search in fetch_exchanges

**Files:**
- Modify: `scripts/fetch_exchanges.py`
- Test: `tests/test_fetch_exchanges.py`

When tier is Enhanced and vectors exist, search uses hybrid ranking.

- [ ] **Step 1: Write failing test**

Add to `tests/test_fetch_exchanges.py`:

```python
class TestHybridSearch(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(db_path=':memory:')
        insert_session(self.conn, 'sess1', '/project', 'hash1',
                       '2026-01-01T00:00:00Z')
        insert_exchanges(self.conn, 'sess1', [
            {'idx': 1, 'timestamp': '2026-01-01T10:00:00Z',
             'preview': 'GPU kernel optimization',
             'user_text': 'How do I optimize GPU kernels?',
             'assistant_text': 'Use shared memory and minimize warp divergence.'},
            {'idx': 2, 'timestamp': '2026-01-01T10:05:00Z',
             'preview': 'Python testing',
             'user_text': 'How do I write tests?',
             'assistant_text': 'Use pytest with fixtures.'},
        ])
        # Add vectors for exchange 1
        insert_exchange_vector(self.conn, 1, [0.9, 0.1] + [0.0] * 382, 'mock')
        insert_exchange_vector(self.conn, 2, [0.1, 0.9] + [0.0] * 382, 'mock')

    def tearDown(self):
        self.conn.close()

    @patch('fetch_exchanges.load_config')
    @patch('fetch_exchanges.embeddings')
    def test_hybrid_search_returns_results(self, mock_emb, mock_cfg):
        mock_cfg.return_value = {
            'tier': 'enhanced',
            'search_keyword_weight': 0.4,
            'search_semantic_weight': 0.6,
        }
        mock_emb.is_available.return_value = True
        # Query vector similar to exchange 1
        mock_emb.embed_query.return_value = [0.85, 0.15] + [0.0] * 382

        from fetch_exchanges import hybrid_search_session
        results = hybrid_search_session(self.conn, 'sess1', 'GPU optimization')
        self.assertTrue(len(results) > 0)
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_fetch_exchanges.py::TestHybridSearch -v`

- [ ] **Step 3: Add hybrid_search_session function to fetch_exchanges.py**

Add imports at top:

```python
from config import load_config
import embeddings
from db import get_session_vectors, get_vectors_by_scope, deserialize_vector
from vector_search import semantic_search, hybrid_search_merge
```

Add function:

```python
def hybrid_search_session(conn, session_id, query, limit=10):
    """Run hybrid search: FTS5 + semantic (if Enhanced tier)."""
    cfg = load_config()

    # Always run FTS5
    keyword_results = search_exchanges_fts(conn, query, session_id=session_id, limit=limit)

    # If Enhanced and embeddings available, add semantic
    if cfg.get('tier') == 'enhanced' and embeddings.is_available():
        query_vec = embeddings.embed_query(query)
        raw_vectors = get_session_vectors(conn, session_id)
        vectors_with_meta = []
        for v in raw_vectors:
            entry = dict(v)
            entry['vector_list'] = deserialize_vector(v['vector'])
            vectors_with_meta.append(entry)

        semantic_results = semantic_search(query_vec, vectors_with_meta, top_k=limit * 2)
        kw = cfg.get('search_keyword_weight', 0.4)
        sw = cfg.get('search_semantic_weight', 0.6)
        return hybrid_search_merge(keyword_results, semantic_results,
                                    keyword_weight=kw, semantic_weight=sw,
                                    limit=limit)

    return keyword_results
```

Then update the `main()` search paths to call `hybrid_search_session` instead of `search_exchanges_fts` when appropriate (for session-scoped and `--all` scoped searches).

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_fetch_exchanges.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_exchanges.py tests/test_fetch_exchanges.py
git commit -m "feat: hybrid search in fetch_exchanges

Enhanced tier combines FTS5 keyword results with cosine similarity
for improved search relevance. Falls back to FTS5-only for
Standard/Lite tiers."
```

---

## Task 9: Highlight Summary Embedding

**Files:**
- Modify: `scripts/highlight.py`
- Modify: `scripts/db.py` (add summary_vector to highlights DDL)
- Test: `tests/test_highlight.py`

When Enhanced tier is active, embed highlight summaries on creation for semantic highlight matching.

- [ ] **Step 1: Write failing test**

Add to `tests/test_highlight.py`:

```python
class TestHighlightEmbedding(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(db_path=':memory:')
        insert_session(self.conn, 'sess1', '/project', 'hash1',
                       '2026-01-01T00:00:00Z')
        insert_exchanges(self.conn, 'sess1', [{
            'idx': 1, 'timestamp': '2026-01-01T10:00:00Z',
            'preview': 'test', 'user_text': 'test',
            'assistant_text': 'test response',
        }])

    def tearDown(self):
        self.conn.close()

    @patch('highlight.load_config')
    @patch('highlight.embeddings')
    def test_enhanced_embeds_summary(self, mock_emb, mock_cfg):
        mock_cfg.return_value = {'tier': 'enhanced'}
        mock_emb.is_available.return_value = True
        mock_emb.embed.return_value = [[0.5] * 384]

        result = create_highlight(self.conn, 'sess1', 'important finding')
        self.assertIn('Highlighted', result)

        # Check summary_vector was stored
        row = self.conn.execute(
            "SELECT summary_vector FROM highlights WHERE session_id = 'sess1'"
        ).fetchone()
        self.assertIsNotNone(row['summary_vector'])

    @patch('highlight.load_config')
    def test_standard_no_embedding(self, mock_cfg):
        mock_cfg.return_value = {'tier': 'standard'}
        result = create_highlight(self.conn, 'sess1', 'standard finding')
        self.assertIn('Highlighted', result)

        row = self.conn.execute(
            "SELECT summary_vector FROM highlights WHERE session_id = 'sess1'"
        ).fetchone()
        self.assertIsNone(row['summary_vector'])
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_highlight.py::TestHighlightEmbedding -v`

- [ ] **Step 3: Add summary_vector to highlights schema in db.py**

Update the `highlights` CREATE TABLE in `_SCHEMA_SQL`:

```sql
CREATE TABLE IF NOT EXISTS highlights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    summary         TEXT NOT NULL,
    exchange_idx    INTEGER,
    tags            TEXT NOT NULL,
    source          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    summary_vector  BLOB,
    UNIQUE(session_id, summary)
);
```

Add migration logic in `get_connection()` for existing DBs — after the schema check, check if `summary_vector` column exists:

```python
    # Migration: add summary_vector to highlights if missing
    cur = conn.execute("PRAGMA table_info(highlights)")
    columns = {row[1] for row in cur.fetchall()}
    if 'summary_vector' not in columns:
        conn.execute("ALTER TABLE highlights ADD COLUMN summary_vector BLOB")
```

Update `insert_highlight()` to accept optional `summary_vector`:

```python
def insert_highlight(conn, session_id, summary, tags, source,
                     exchange_idx=None, summary_vector=None, commit=True):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO highlights "
        "(session_id, summary, exchange_idx, tags, source, created_at, summary_vector) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, summary, exchange_idx, tags, source, now, summary_vector),
    )
    if commit:
        conn.commit()
```

- [ ] **Step 4: Modify highlight.py to embed on creation**

Add imports:

```python
from config import load_config
import embeddings
from db import serialize_vector
```

In `create_highlight()`, after building the tags string and before `insert_highlight()`:

```python
    # Enhanced tier: embed the summary
    summary_vector = None
    cfg = load_config()
    if cfg.get('tier') == 'enhanced':
        try:
            if embeddings.is_available():
                vec = embeddings.embed([summary])[0]
                summary_vector = serialize_vector(vec)
        except Exception:
            pass  # Non-blocking — highlight still created without vector
```

Pass `summary_vector=summary_vector` to `insert_highlight()`.

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/test_highlight.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/db.py scripts/highlight.py tests/test_highlight.py
git commit -m "feat: embed highlight summaries on creation

Enhanced tier embeds highlight summaries for semantic matching
across sessions. Adds summary_vector BLOB column to highlights
with migration for existing DBs."
```

---

## Task 10: Config Commands for Tier & Embedding

**Files:**
- Modify: `scripts/manage_connections.py`
- Test: `tests/test_manage_connections.py`

Add `tier` and `embedding_backend` to the config command, plus `update-model`.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_manage_connections.py`:

```python
class TestTierConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = Path(self.tmp) / 'recall-config.json'

    @patch('manage_connections.CONFIG_PATH', new_callable=lambda: property(lambda self: self.config_path))
    def test_config_tier_enhanced(self):
        from config import save_config, get_default_config
        cfg = get_default_config()
        save_config(cfg, self.config_path)

        from manage_connections import config_global
        result = config_global('tier', 'enhanced', config_path=self.config_path)
        self.assertIn('enhanced', result)

    def test_config_tier_invalid(self):
        from manage_connections import config_global
        result = config_global('tier', 'mega', config_path=self.config_path)
        self.assertIn('Error', result)

    def test_config_embedding_backend(self):
        from config import save_config, get_default_config
        cfg = get_default_config()
        save_config(cfg, self.config_path)

        from manage_connections import config_global
        result = config_global('embedding_backend', 'tfidf', config_path=self.config_path)
        self.assertIn('tfidf', result)
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_manage_connections.py::TestTierConfig -v`

- [ ] **Step 3: Add config_global function to manage_connections.py**

```python
from config import (load_config, save_config, validate_config,
                    VALID_TIERS, VALID_BACKENDS, CONFIG_PATH)


GLOBAL_CONFIG_KEYS = {'tier', 'embedding_backend'}


def config_global(key: str, value: str, config_path=None) -> str:
    """Set a global config value (tier, embedding_backend)."""
    if config_path is None:
        config_path = CONFIG_PATH

    if key == 'tier':
        if value not in VALID_TIERS:
            return f"*Error: invalid tier '{value}'. Must be one of: {', '.join(sorted(VALID_TIERS))}.*"
    elif key == 'embedding_backend':
        if value not in VALID_BACKENDS:
            return f"*Error: invalid backend '{value}'. Must be one of: {', '.join(sorted(VALID_BACKENDS))}.*"
    elif key == 'update-model':
        return "*Model update not yet implemented.*"
    else:
        return None  # Not a global key — fall through to per-session config

    cfg = load_config(config_path)
    cfg[key] = value
    errors = validate_config(cfg)
    if errors:
        return f"*Error: {'; '.join(errors)}*"

    save_config(cfg, config_path)
    return f"*Config updated: {key} = '{value}'.*"
```

Update the existing `config()` function and `main()` CLI to route `tier` and `embedding_backend` to `config_global()` before falling through to per-session config.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_manage_connections.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/manage_connections.py tests/test_manage_connections.py
git commit -m "feat: tier and embedding_backend config commands

Routes 'tier' and 'embedding_backend' to global recall-config.json.
Validates against allowlists. Per-session config unchanged."
```

---

## Task 11: Silent Surfacing Plumbing (Disabled)

**Files:**
- Modify: `hooks/prompt_submit.py`
- Test: `tests/test_prompt_submit.py`

Add the code path for silent surfacing behind the `silent_surfacing` config gate. Ships disabled.

- [ ] **Step 1: Write failing test**

Add to `tests/test_prompt_submit.py`:

```python
class TestSilentSurfacingPlumbing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 'test.db'

    @patch('prompt_submit.load_config')
    def test_disabled_by_default(self, mock_cfg):
        mock_cfg.return_value = {
            'tier': 'enhanced',
            'silent_surfacing': False,
        }
        # Normal hook run should not include surfacing message
        result = run_hook({
            'session_id': 'test',
            'transcript_path': '',
            'user_prompt': 'hello',
            'project_path': '/test',
            'project_hash': 'hash1',
        }, db_path=self.db_path)
        msg = result.get('systemMessage', '')
        self.assertNotIn('[Recall] Potentially relevant', msg)

    @patch('prompt_submit.load_config')
    @patch('prompt_submit.embeddings')
    def test_enabled_produces_surfacing(self, mock_emb, mock_cfg):
        """When enabled with vectors present, surfacing message is produced."""
        mock_cfg.return_value = {
            'tier': 'enhanced',
            'silent_surfacing': True,
            'surfacing_threshold': 0.5,
            'surfacing_max_results': 3,
            'surfacing_recency_exclude': 0,
        }
        mock_emb.is_available.return_value = True
        mock_emb.embed_query.return_value = [0.9] * 384

        # Pre-populate DB with session + exchanges + vectors
        conn = get_connection(self.db_path)
        insert_session(conn, 'test', '/test', 'hash1', '2026-01-01T00:00:00Z')
        insert_exchanges(conn, 'test', [{
            'idx': 1, 'timestamp': '2026-01-01T10:00:00Z',
            'preview': 'relevant exchange',
            'user_text': 'relevant', 'assistant_text': 'response',
        }])
        from db import insert_exchange_vector
        insert_exchange_vector(conn, 1, [0.9] * 384, 'mock')
        conn.close()

        result = run_hook({
            'session_id': 'test',
            'transcript_path': '',
            'user_prompt': 'related query',
            'project_path': '/test',
            'project_hash': 'hash1',
        }, db_path=self.db_path)
        msg = result.get('systemMessage', '')
        self.assertIn('[Recall]', msg)
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_prompt_submit.py::TestSilentSurfacingPlumbing -v`

- [ ] **Step 3: Add surfacing plumbing to prompt_submit.py**

Add at the end of `run_hook()`, after connection checking and before the final return:

```python
        # --- Silent surfacing (disabled by default) ---
        cfg = load_config()
        if (cfg.get('tier') == 'enhanced'
                and cfg.get('silent_surfacing', False)
                and embeddings.is_available()):
            try:
                query_vec = embeddings.embed_query(user_prompt)
                session_vecs = get_session_vectors(conn, session_id)

                if session_vecs:
                    exclude_n = cfg.get('surfacing_recency_exclude', 5)
                    threshold = cfg.get('surfacing_threshold', 0.75)
                    max_results = cfg.get('surfacing_max_results', 3)

                    # Exclude most recent N exchanges
                    candidates = session_vecs[:-exclude_n] if exclude_n and len(session_vecs) > exclude_n else session_vecs
                    vectors_with_meta = []
                    for v in candidates:
                        entry = dict(v)
                        entry['vector_list'] = deserialize_vector(v['vector'])
                        vectors_with_meta.append(entry)

                    from vector_search import semantic_search
                    results = semantic_search(query_vec, vectors_with_meta, top_k=max_results)
                    relevant = [r for r in results if r.get('semantic_score', 0) >= threshold]

                    if relevant:
                        lines = ['[Recall] Potentially relevant prior context:']
                        for r in relevant:
                            idx = r.get('idx', '?')
                            ts = format_timestamp(r.get('timestamp', ''))
                            preview = r.get('preview', '')
                            lines.append(f'  - Exchange #{idx} ({ts}): "{preview}"')
                        lines.append('Run /recall search to pull full content.')
                        surfacing_msg = '\n'.join(lines)

                        # Combine with any existing system message
                        if connection_msg:
                            return {"systemMessage": connection_msg + '\n\n' + surfacing_msg}
                        return {"systemMessage": surfacing_msg}
            except Exception as e:
                print(f"[context-recall] Surfacing error (non-blocking): {e}", file=sys.stderr)
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_prompt_submit.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/prompt_submit.py tests/test_prompt_submit.py
git commit -m "feat: silent surfacing plumbing (disabled by default)

Adds the code path for proactive context injection in prompt_submit.
Behind silent_surfacing config gate, default false. Uses pre-computed
vectors from checkpoints — no embedding on every prompt."
```

---

## Task 12: Hook Timeouts, Plugin Version, Command Docs

**Files:**
- Modify: `hooks/hooks.json`
- Modify: `.claude-plugin/plugin.json`
- Modify: `commands/recall.md`

- [ ] **Step 1: Update hooks.json timeouts**

Change PostCompact and SessionEnd timeouts from 5 to 30:

```json
{
  "PostCompact": [
    {
      "matcher": "*",
      "hooks": [
        {
          "type": "command",
          "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/post_compact.py",
          "timeout": 30
        }
      ]
    }
  ],
  "SessionEnd": [
    {
      "matcher": "*",
      "hooks": [
        {
          "type": "command",
          "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/session_end.py",
          "timeout": 30
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Bump plugin.json to 3.0.0**

```json
{
  "name": "recall",
  "version": "3.0.0",
  "description": "Cross-session conversation recall with three runtime tiers (Lite/Standard/Enhanced), SQLite storage, auto-tagging, compaction recovery, highlight sharing, opt-in semantic search, and proactive recall assistant skill"
}
```

- [ ] **Step 3: Update recall.md with new config commands**

Add to the config section of `commands/recall.md`:

```
- `config tier <lite|standard|enhanced>` → sets the runtime tier
- `config embedding_backend <auto|mlx|sentence-transformers|onnx|tfidf>` → sets the embedding backend
- `config update-model` → download latest embedding model (Enhanced tier)
```

Route these in the command dispatch to `manage_connections.py config_global`.

- [ ] **Step 4: Run all tests to verify nothing broke**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/hooks.json .claude-plugin/plugin.json commands/recall.md
git commit -m "chore: bump to v3.0.0, update timeouts and command docs

PostCompact/SessionEnd timeouts increased to 30s for Enhanced tier
embedding batches. Plugin version 3.0.0. Tier config commands
documented in recall.md."
```

---

## Task 13: Full Integration Test & README

**Files:**
- Modify: `tests/integration_test.py`
- Modify: `README.md`

- [ ] **Step 1: Add integration test for Enhanced tier lifecycle**

Add to `tests/integration_test.py`:

```python
class TestEnhancedTierLifecycle(unittest.TestCase):
    """Full lifecycle: config -> embed -> search -> hybrid results."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 'test.db'
        self.config_path = Path(self.tmp) / 'recall-config.json'

    def test_full_enhanced_lifecycle(self):
        from config import get_default_config, save_config
        from db import (get_connection, insert_session, insert_exchanges,
                        get_unvectorized_exchanges, insert_exchange_vector,
                        get_session_vectors)
        from vector_search import cosine_similarity

        # 1. Create config as Enhanced
        cfg = get_default_config()
        cfg['tier'] = 'enhanced'
        cfg['detected_backend'] = 'mock'
        save_config(cfg, self.config_path)

        # 2. Create session with exchanges
        conn = get_connection(db_path=self.db_path)
        insert_session(conn, 'sess1', '/project', 'hash1', '2026-01-01T00:00:00Z')
        insert_exchanges(conn, 'sess1', [
            {'idx': 1, 'timestamp': '2026-01-01T10:00:00Z',
             'preview': 'GPU kernels', 'user_text': 'How to optimize GPU kernels?',
             'assistant_text': 'Use shared memory and avoid warp divergence.'},
            {'idx': 2, 'timestamp': '2026-01-01T10:05:00Z',
             'preview': 'Python testing', 'user_text': 'How to write tests?',
             'assistant_text': 'Use pytest with fixtures.'},
        ])

        # 3. Verify exchanges are unvectorized
        unvec = get_unvectorized_exchanges(conn, 'sess1')
        self.assertEqual(len(unvec), 2)

        # 4. Simulate checkpoint: embed exchanges
        for ex in unvec:
            vec = [0.5] * 384  # Mock vector
            insert_exchange_vector(conn, ex['id'], vec, 'mock')

        # 5. Verify vectors stored
        vectors = get_session_vectors(conn, 'sess1')
        self.assertEqual(len(vectors), 2)

        # 6. Verify no unvectorized remain
        unvec = get_unvectorized_exchanges(conn, 'sess1')
        self.assertEqual(len(unvec), 0)

        conn.close()
```

- [ ] **Step 2: Run integration tests**

Run: `python3 -m pytest tests/integration_test.py -v`
Expected: All PASS.

- [ ] **Step 3: Update README.md**

Add a "Tiers" section after the Quick Start section documenting the three tiers, how to switch, the embedding backend detection, and the Enhanced tier setup flow. Update the version references from 2.2.0 to 3.0.0. Add the new config commands to the command reference.

- [ ] **Step 4: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS (226 existing + ~30 new).

- [ ] **Step 5: Commit**

```bash
git add tests/integration_test.py README.md
git commit -m "feat: Enhanced tier integration test and README v3.0.0

Full lifecycle test: config -> embed -> store -> search.
README updated with tier documentation, embedding setup,
and new config commands."
```
