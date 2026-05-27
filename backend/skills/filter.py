"""Context-aware skill filtering.

Two activation mechanisms (checked in order per skill):

1. `paths:` — comma-separated glob patterns (supports **).
   If ANY extracted file path from the user message matches ANY pattern,
   the skill is force-included regardless of `when_to_use`.

2. `when_to_use` — natural language trigger description.
   If set, the skill is included only when extracted keywords appear in
   the user message.  Skills without `when_to_use` are always included
   (backward compatible).
"""
import re


# ---------------------------------------------------------------------------
# Glob matching (supports **, *, ?)
# ---------------------------------------------------------------------------

def _compile_glob(pattern: str) -> re.Pattern:
    """Convert a glob pattern with optional ** into a compiled regex.

    **/ (or ** at end) matches zero or more path segments (prefix optional),
    so **/*.py matches both main.py and src/api/main.py.
    """
    # Split on **, */, *, ? — capture the delimiters
    parts = re.split(r'(\*\*(?:[/\\])?|\*|\?)', pattern)
    regex_parts = []
    for part in parts:
        if part in ('**/', '**\\'):
            # **/ → zero-or-more path-segment prefix (e.g. **/*.py matches main.py)
            regex_parts.append('(?:.*/)?')
        elif part == '**':
            # ** at end of pattern → match anything including slashes
            regex_parts.append('.*')
        elif part == '*':
            regex_parts.append('[^/]*')
        elif part == '?':
            regex_parts.append('[^/]')
        else:
            regex_parts.append(re.escape(part))
    return re.compile('^' + ''.join(regex_parts) + '$', re.IGNORECASE)


def _paths_match(paths_spec: str, candidate_paths: list[str]) -> bool:
    """Return True if any candidate path matches any pattern in paths_spec."""
    patterns = [p.strip() for p in paths_spec.split(',') if p.strip()]
    if not patterns or not candidate_paths:
        return False
    compiled = [_compile_glob(p) for p in patterns]
    for raw_path in candidate_paths:
        path = raw_path.replace('\\', '/')
        basename = path.rsplit('/', 1)[-1]
        for pat in compiled:
            if pat.match(path) or pat.match(basename):
                return True
    return False


# ---------------------------------------------------------------------------
# Path extraction from user message
# ---------------------------------------------------------------------------

_KNOWN_EXTS = (
    'py|js|ts|tsx|jsx|go|rs|java|cpp|c|h|rb|php|sh|bash|'
    'md|json|yaml|yml|toml|sql|vue|svelte|css|html|xml|'
    'swift|kt|scala|r|lua|ex|exs|clj|hs|ml|fs|cs|vb'
)
_PATH_RE = re.compile(
    r'(?:[\w./\-]+/)?[\w\-*.]+\.(?:' + _KNOWN_EXTS + r')\b',
    re.IGNORECASE,
)


def _extract_paths(message: str) -> list[str]:
    """Extract file-path-like strings from a user message."""
    return _PATH_RE.findall(message)


# ---------------------------------------------------------------------------
# Keyword extraction (for when_to_use matching)
# ---------------------------------------------------------------------------

def _extract_keywords(text: str) -> set[str]:
    """Extract matchable keywords from a when_to_use string.

    - ASCII: words ≥ 3 chars (lowercased, hyphens allowed)
    - Chinese: 2-char bigrams via sliding window over hanzi characters
    """
    result: set[str] = set()
    for w in re.findall(r'[a-zA-Z][a-zA-Z\-_]{2,}', text):
        result.add(w.lower())
    hanzi = re.findall(r'[一-鿿]', text)
    for i in range(len(hanzi) - 1):
        result.add(hanzi[i] + hanzi[i + 1])
    return result


# ---------------------------------------------------------------------------
# Main filter
# ---------------------------------------------------------------------------

def filter_skills_by_context(skills: list[dict], user_message: str) -> list[dict]:
    """Return the subset of skills relevant to user_message.

    Per-skill inclusion logic:
      1. `paths:` set AND any extracted path matches  → include (force)
      2. `when_to_use` not set                        → include (always)
      3. `when_to_use` set AND keywords match message → include
      4. otherwise                                    → exclude
    """
    if not user_message or not skills:
        return skills

    msg = user_message.lower()
    candidate_paths = _extract_paths(user_message)

    result = []
    for s in skills:
        paths_spec = (s.get("paths") or "").strip()
        if paths_spec:
            # paths: defined → include ONLY when paths match (when_to_use ignored)
            if _paths_match(paths_spec, candidate_paths):
                result.append(s)
            continue

        # No paths: → apply when_to_use filter
        when_to_use = (s.get("when_to_use") or "").strip()
        if not when_to_use:
            result.append(s)
            continue
        keywords = _extract_keywords(when_to_use)
        if not keywords or any(kw in msg for kw in keywords):
            result.append(s)

    return result
