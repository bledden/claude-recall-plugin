#!/usr/bin/env python3
"""Hybrid auto-tagging system for Claude Context Recall plugin.

Provides term-frequency extraction, technical term heuristic, and DB
integration for automatic and manual session tagging.

Pure stdlib — no external dependencies.
"""

import re
from typing import Dict, List

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_AUTO_TAGS_PER_SESSION = 10
AUTO_TAG_MIN_FREQUENCY = 3

# ---------------------------------------------------------------------------
# Filter lists
# ---------------------------------------------------------------------------

STOPWORDS: frozenset = frozenset({
    # Articles / determiners
    "the", "a", "an", "this", "that", "these", "those", "its",
    # Conjunctions
    "and", "but", "or", "nor", "for", "yet", "so", "both", "either",
    "neither", "not", "only", "whether",
    # Prepositions
    "in", "on", "at", "to", "of", "by", "as", "up", "out", "off",
    "over", "under", "into", "onto", "from", "with", "about", "after",
    "before", "between", "through", "during", "without", "within",
    "along", "across", "behind", "beyond", "near", "down", "per",
    "via", "than",
    # Pronouns
    "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "they", "them", "their", "who", "what", "which",
    "all", "each", "any", "some", "such", "one", "two", "three",
    # Common verbs / aux
    "be", "is", "are", "was", "were", "been", "being",
    "have", "has", "had", "having",
    "do", "does", "did", "done",
    "will", "would", "shall", "should", "may", "might", "must", "can",
    "could", "need", "used",
    "get", "got", "make", "made", "use", "let", "put", "set", "take",
    "come", "go", "run", "see", "know", "want", "give", "say", "tell",
    "think", "try", "call", "keep", "find", "add", "show", "move",
    "change", "check", "work", "start", "stop", "end", "pass", "read",
    "write", "load", "save", "copy", "move", "send", "receive", "open",
    "close", "create", "delete", "remove", "update", "build", "test",
    # Common adjectives / adverbs
    "also", "just", "even", "still", "then", "when", "where", "how",
    "well", "very", "more", "most", "much", "many", "few", "less",
    "same", "other", "another", "different", "new", "old", "large",
    "small", "big", "long", "short", "high", "low", "good", "bad",
    "right", "left", "first", "last", "next", "own", "main", "specific",
    "current", "real", "simple", "possible", "important", "sure",
    "able", "like", "better", "best", "too", "often", "now", "here",
    "there", "always", "never", "usually", "generally", "typically",
    # Conversational filler
    "yeah", "okay", "actually", "really", "maybe", "probably",
    "looks", "seems", "please", "thanks", "thank", "note", "just",
    "basically", "honestly", "literally", "essentially", "exactly",
    "definitely", "absolutely", "certainly", "quite", "rather",
    "however", "although", "though", "because", "since", "while",
    "instead", "otherwise", "therefore", "thus", "hence", "indeed",
    "else", "already", "again", "back", "way", "case", "thing",
    "things", "anything", "nothing", "something", "everything",
    "anyone", "someone", "everyone", "every", "part", "example",
    "issue", "point", "step", "lot", "bit", "stuff",
})

GENERIC_PROGRAMMING_TERMS: frozenset = frozenset({
    # Code structure
    "function", "method", "class", "module", "package", "library",
    "variable", "constant", "parameter", "argument", "attribute",
    "property", "field", "member",
    # Control flow
    "loop", "block", "branch", "condition", "statement", "expression",
    "operator", "operand",
    # Data
    "value", "values", "data", "input", "output", "result", "results",
    "object", "instance", "pointer", "reference", "index", "key",
    "array", "list", "dict", "tuple", "set",
    # Types
    "type", "types", "string", "integer", "float", "boolean", "byte",
    "buffer", "struct", "enum",
    # Error handling
    "error", "errors", "exception", "warning", "debug", "assert",
    "check", "handle",
    # I/O and misc
    "file", "files", "path", "directory", "line", "lines", "size",
    "number", "count", "flag", "mode", "name", "version", "config",
    "option", "format", "print", "log", "import", "export", "code",
    "return", "call", "calls", "api", "interface", "implementation",
    "process", "thread", "queue", "stack",
})

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r'[a-z0-9][a-z0-9_-]*[a-z0-9]|[a-z0-9]{3,}')


def tokenize(text: str) -> List[str]:
    """Split text into lowercase tokens, preserving hyphens/underscores within words.

    Tokens shorter than 3 characters are excluded.

    Args:
        text: Raw text to tokenize.

    Returns:
        List of token strings.
    """
    lowered = text.lower()
    return [tok for tok in _TOKEN_RE.findall(lowered) if len(tok) >= 3]


# ---------------------------------------------------------------------------
# Technical term heuristic
# ---------------------------------------------------------------------------

def is_technical_term(term: str) -> bool:
    """Return True if term qualifies as a technical tag candidate.

    Terms containing '-', '_', or any digit are always technical.
    Otherwise the term must not appear in STOPWORDS or GENERIC_PROGRAMMING_TERMS.

    Args:
        term: A single token string.

    Returns:
        bool
    """
    if '-' in term or '_' in term or any(c.isdigit() for c in term):
        return True
    return term not in STOPWORDS and term not in GENERIC_PROGRAMMING_TERMS


# ---------------------------------------------------------------------------
# Term extraction
# ---------------------------------------------------------------------------

def extract_terms(texts: List[str]) -> Dict[str, int]:
    """Tokenize all texts, count frequency, filter stopwords.

    Args:
        texts: List of raw text strings.

    Returns:
        Dict mapping term -> occurrence count (stopwords excluded).
    """
    counts: Dict[str, int] = {}
    for text in texts:
        for token in tokenize(text):
            if token not in STOPWORDS:
                counts[token] = counts.get(token, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Tag selection
# ---------------------------------------------------------------------------

def select_tags(
    term_counts: Dict[str, int],
    max_tags: int = MAX_AUTO_TAGS_PER_SESSION,
    min_frequency: int = AUTO_TAG_MIN_FREQUENCY,
) -> List[str]:
    """Filter, rank, and cap tag candidates.

    Filters by min_frequency and is_technical_term, sorts by frequency
    descending, then caps at max_tags.

    Args:
        term_counts: Dict of {term: count} from extract_terms.
        max_tags: Maximum number of tags to return.
        min_frequency: Minimum occurrence count required.

    Returns:
        List of selected tag strings, highest frequency first.
    """
    candidates = [
        (term, count)
        for term, count in term_counts.items()
        if count >= min_frequency and is_technical_term(term)
    ]
    candidates.sort(key=lambda tc: tc[1], reverse=True)
    return [term for term, _ in candidates[:max_tags]]


# ---------------------------------------------------------------------------
# Session text extraction
# ---------------------------------------------------------------------------

def get_session_texts(exchanges: List[Dict]) -> List[str]:
    """Extract user_text and assistant_text from exchange dicts.

    Args:
        exchanges: List of exchange dicts (as returned by db.get_exchanges).

    Returns:
        List of non-empty text strings.
    """
    texts: List[str] = []
    for ex in exchanges:
        user_text = ex.get("user_text") or ""
        assistant_text = ex.get("assistant_text") or ""
        if user_text:
            texts.append(user_text)
        if assistant_text:
            texts.append(assistant_text)
    return texts


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_auto_tags(
    exchanges: List[Dict],
    max_tags: int = MAX_AUTO_TAGS_PER_SESSION,
    min_frequency: int = AUTO_TAG_MIN_FREQUENCY,
) -> List[str]:
    """Compute auto-tags for a list of exchanges.

    Pipeline: get_session_texts -> extract_terms -> select_tags.

    Args:
        exchanges: List of exchange dicts with user_text / assistant_text fields.
        max_tags: Maximum tags to return.
        min_frequency: Minimum term frequency required.

    Returns:
        List of tag strings, highest frequency first.
    """
    texts = get_session_texts(exchanges)
    term_counts = extract_terms(texts)
    return select_tags(term_counts, max_tags=max_tags, min_frequency=min_frequency)
