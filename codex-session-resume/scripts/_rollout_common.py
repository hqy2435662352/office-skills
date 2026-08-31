"""Shared structural helpers (V1.1) for the codex-session-resume scripts.

`preprocess_session.py` and `index_sessions.py` both operate on Codex
rollout-*.jsonl archives; this module holds the small, purely structural
helpers they share. It is intentionally tiny — no parser class hierarchy, no
event registry, no protocol framework.

Important conservative rule (V1.1): client-injected blocks are only removed
when their FULL boundary is recognized. An incomplete or unknown boundary is
kept verbatim — never delete user text on uncertainty.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

# Pairs that define a complete injected block: (start, end).
# A block is only stripped when BOTH markers are present.
CLIENT_BLOCK_PAIRS = (
    ("<recommended_plugins>", "</recommended_plugins>"),
    ("<environment_context>", "</environment_context>"),
    ("<permissions", "</permissions>"),
    ("<permission_profile", "</permission_profile>"),
)

# The AGENTS.md instruction block seen in real Codex Desktop rollouts:
#   # AGENTS.md instructions
#   <INSTRUCTIONS>
#   ...
#   </INSTRUCTIONS>
AGENTS_PREFIX = "# AGENTS.md instructions"
AGENTS_OPEN = "<INSTRUCTIONS>"
AGENTS_CLOSE = "</INSTRUCTIONS>"


def strip_client_blocks(text: str) -> tuple[str, int]:
    """Remove client-injected blocks with complete recognized boundaries.

    Returns (remaining_text, blocks_stripped). Blocks may be separated by blank
    lines or leading whitespace (real Codex Desktop message items join blocks
    with "\\n\\n"). Text after a closed block is preserved. A block whose closing
    marker is missing is NOT removed, and everything from that point on is kept
    (including any leading whitespace). Never deletes user text on doubt.
    """
    stripped = 0
    remaining = text
    while True:
        # skip whitespace only when a complete block follows it
        candidate = remaining.lstrip(" \t\r\n")
        whitespace = remaining[: len(remaining) - len(candidate)]
        matched = False
        for start, end in CLIENT_BLOCK_PAIRS:
            if candidate.startswith(start):
                close = candidate.find(end)
                if close == -1:
                    return remaining, stripped  # incomplete boundary -> keep all
                remaining = candidate[close + len(end):]
                stripped += 1
                matched = True
                break
        if not matched and candidate.startswith(AGENTS_PREFIX):
            s = candidate.find(AGENTS_OPEN)
            e = candidate.find(AGENTS_CLOSE)
            if s == -1 or e == -1 or e <= s:
                return remaining, stripped  # incomplete AGENTS block -> keep all
            remaining = candidate[e + len(AGENTS_CLOSE):]
            stripped += 1
            matched = True
        if not matched:
            # no block at the front: restore the whitespace we skipped so user
            # text (and its indentation) is preserved verbatim
            return whitespace + candidate, stripped


def truncate_head_tail(text: str, cap: int, source_line: int) -> str:
    """Deterministic truncation with an explicit marker; unchanged when <= cap."""
    if len(text) <= cap:
        return text
    head_len = int(cap * 0.6)
    tail_len = cap - head_len
    head = text[:head_len]
    tail = text[-tail_len:]
    marker = (
        f"\n...<TRUNCATED: {len(text)} chars kept {head_len}+{tail_len}; "
        f"raw source line {source_line}>...\n"
    )
    return head + marker + tail


def content_items_text(items: Any, is_user: bool, source_line: int,
                       unknown_cap: int, stats: dict | None = None) -> str:
    """Flatten message content items to text, preserving everything.

    For user items, client-injected blocks with complete boundaries are stripped
    and counted; any remaining text (including user text glued after a closed
    block, and unclosed blocks kept verbatim) is preserved. Items without a text
    field are kept as truncated JSON. When `stats` is given, counters
    "client_blocks_stripped" and "truncated_events" are updated in place.
    """
    if not isinstance(items, list):
        items = [items]
    parts: list[str] = []
    if stats is None:
        stats = {}
    for item in items:
        if not isinstance(item, dict):
            parts.append(truncate_head_tail(json.dumps(item, ensure_ascii=False),
                                            unknown_cap, source_line))
            if len(json.dumps(item, ensure_ascii=False)) > unknown_cap:
                stats["truncated_events"] = stats.get("truncated_events", 0) + 1
            continue
        if "text" in item:
            t = item["text"]
            t = t if isinstance(t, str) else str(t)
        else:
            parts.append(truncate_head_tail(json.dumps(item, ensure_ascii=False),
                                            unknown_cap, source_line))
            if len(json.dumps(item, ensure_ascii=False)) > unknown_cap:
                stats["truncated_events"] = stats.get("truncated_events", 0) + 1
            continue
        if is_user:
            t, n = strip_client_blocks(t)
            if n:
                stats["client_blocks_stripped"] = stats.get("client_blocks_stripped", 0) + n
        # text items are preserved verbatim; callers apply their own caps
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def extract_session_meta(payload: Any) -> dict[str, Any]:
    """Extract the stable session metadata subset from a session_meta payload.

    Unknown fields stay absent (never guessed); callers fill the rest.
    """
    meta: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return meta

    def scalar(key: str) -> Any:
        v = payload.get(key)
        return v if isinstance(v, (str, int, float, bool)) else None

    meta["session_id"] = scalar("session_id") or scalar("id")
    meta["cwd"] = scalar("cwd")
    meta["model_provider"] = scalar("model_provider")
    meta["originator"] = scalar("originator")
    meta["started_at"] = scalar("timestamp")
    meta["cli_version"] = scalar("cli_version")
    meta["source"] = scalar("source")
    meta["thread_source"] = scalar("thread_source")
    meta["history_mode"] = scalar("history_mode")
    git = payload.get("git")
    if isinstance(git, dict) and git:
        meta["git"] = git
    return meta


def extract_user_message_text(payload: Any, source_type: str, source_line: int,
                              caps: dict, stats: dict | None = None) -> str | None:
    """Return the cleaned text of a user message record, or None when the
    message is pure client-injected context (nothing user-authored remains).

    Supports both the canonical response_item/message role=user form and the
    legacy event_msg/user_message form.
    """
    if source_type == "event_msg" and isinstance(payload, dict) \
            and payload.get("type") == "user_message":
        msg = payload.get("message")
        text = msg if isinstance(msg, str) else ""
        text = text.strip()
        return text or None
    if source_type == "response_item" and isinstance(payload, dict) \
            and payload.get("role") == "user":
        text = content_items_text(payload.get("content"), is_user=True,
                                  source_line=source_line,
                                  unknown_cap=caps.get("unknown", 8000), stats=stats)
        text = text.strip()
        return text or None
    return None


def parse_timestamp(ts: Any) -> datetime | None:
    """Best-effort ISO timestamp parse; returns None on anything else."""
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None