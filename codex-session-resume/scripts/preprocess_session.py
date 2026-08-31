#!/usr/bin/env python3
"""
scripts/preprocess_session.py — deterministic Codex rollout -> clean session representation.

Structural preprocessor for the `codex-session-resume` skill. Extracts a traceable,
normalized event stream from a Codex `rollout-*.jsonl` archive. It performs NO
semantic judgment: what is important, what is done, what is pending, and what to
do next are questions for the Agent reading the clean output.

Pipeline boundaries:
  raw rollout             -> this script (structural facts only)
  clean.jsonl/meta/stats  -> Agent reading rules (SKILL.md)
  current workspace       -> final source of truth after resume

Usage:
  python scripts/preprocess_session.py --input <rollout.jsonl> --output <out-dir>
  python scripts/preprocess_session.py --input rollout.jsonl --output out \
      --cap-tool-output 40000 --cap-tool-input 16000 \
      --cap-file-change 8000 --cap-unknown 8000

Output (out-dir/):
  clean.jsonl   normalized event stream (one JSON object per line, UTF-8)
  meta.json     session metadata (unknown fields are null, never guessed)
  stats.json    cleaning diagnostics (fail-visible accounting)

Guarantees:
  * input is opened read-only; nothing under ~/.codex is ever touched
  * deterministic: same input bytes -> byte-identical outputs (no clock/random)
  * UTF-8 in/out; Windows paths and Chinese text preserved verbatim
  * streaming read: hundreds-of-MB rollouts work without loading them whole
  * every normalized event keeps source_line / source_type / source_ordinal so a
    reader can return to the raw rollout for targeted forensic backfill
  * unknown record types are NEVER silently dropped: kept as kind=unknown and
    counted in stats.json (including unknown item types inside item_completed)

Normalization policy (V1, grounded in openai/codex rollout_reconstruction.rs and
cli-continues src/parsers/codex.ts):

  KEEP   real user messages (client-injected blocks stripped, counted; only
         blocks with COMPLETE recognized boundaries are removed — an incomplete
         or unknown boundary keeps the text verbatim, never deleting on doubt)
         assistant visible messages (commentary / final_answer phases kept)
         custom_tool_call / function_call (+ output, paired by call_id in two
         passes: chronological emit, then a backfill pass from the call_id map)
         FileChange items inside event_msg/item_completed (they exist ONLY there)
         turn_context (trimmed: turn_id, model, cwd, workspace_roots, policy...)
         lifecycle events: task_complete / turn_completed / turn_aborted
         thread_rolled_back (turn-aware: invalidates whole user-turn segments
         bounded by turn_id; confidence exact/approximate when no turn context)
         compacted (marker only: summary + window meta + replacement_history
                    count; the full replacement_history is NOT re-injected)
         world_state (marker only: full / keys / bytes / sha256; payload omitted;
                    byte counts are UTF-8 bytes, not char counts)

  DROP+COUNT  response_item/reasoning and event_msg/agent_reasoning (hidden or
              encrypted chain-of-thought; never restored, rewritten or inferred)
              token_count accounting events
              item_completed duplicates of response items — but ONLY the known
              duplicate types (Reasoning, AgentMessage, UserMessage,
              CommandExecution); an unknown item type inside item_completed is
              emitted as kind=unknown (fail-visible, never silent-drop)
              task_started / thread_settings_applied
              response_item/message with role=developer (environment boilerplate)
              user content reduced to nothing by client-block stripping
              (emitted as kind=client_context instead of silently dropped)

  UNKNOWN  any top-level type / payload type / role / item_completed item type
           outside the sets above, and malformed JSON lines -> kind=unknown,
           counted, with source line.

Truncation is deterministic (head 60% + tail 40%, explicit marker that records
original length and raw source line) and only applies to oversized payloads.

Exit codes: 0 = ok; 1 = usage / input error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from _rollout_common import (
    content_items_text,
    extract_session_meta,
    strip_client_blocks,
    truncate_head_tail,
)

# re-export for tests / callers that historically imported these here
__all__ = [
    "Preprocessor",
    "extract_exec_command",
    "main",
    "strip_client_blocks",
    "truncate_head_tail",
]

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

CAP_TOOL_INPUT = 16000
CAP_TOOL_OUTPUT = 40000
CAP_FILE_CHANGE = 8000  # per path entry inside a FileChange
CAP_UNKNOWN = 8000
CAP_ASSISTANT = 40000
CAP_LIFECYCLE_MSG = 1000
CAP_COMPACT_MSG = 2000

KNOWN_TOP_TYPES = {
    "session_meta",
    "event_msg",
    "response_item",
    "turn_context",
    "world_state",
    "compacted",
}

KNOWN_RESPONSE_ITEM_TYPES = {
    "message",
    "reasoning",
    "custom_tool_call",
    "custom_tool_call_output",
    "function_call",
    "function_call_output",
    "web_search_call",
}

KNOWN_EVENT_MSG_TYPES = {
    "item_completed",
    "token_count",
    "task_started",
    "task_complete",
    "turn_completed",
    "turn_aborted",
    "thread_settings_applied",
    "thread_rolled_back",
    "compacted",
    # legacy CLI formats still emitted by some Codex builds
    "user_message",
    "agent_message",
    "assistant_message",
    "agent_reasoning",
    # real-sample evidence (legacy codex-tui rollouts): web search completion
    # marker paired with response_item/web_search_call via call_id<->id
    "web_search_end",
}

ITEM_COMPLETED_DUPLICATE_TYPES = {
    "Reasoning", "AgentMessage", "UserMessage", "CommandExecution",
    # real-sample evidence (rollout 01a051ef): McpToolCall item_completed entries
    # mirror the response_item function_call stream 1:1 (5/5 in the sample)
    "McpToolCall",
}
FILE_CHANGE_TYPE = "FileChange"
LIFECYCLE_EVENTS = {"task_complete", "turn_completed", "turn_aborted"}


# ---------------------------------------------------------------------------
# preprocessor
# ---------------------------------------------------------------------------

class Preprocessor:
    def __init__(self, caps: dict[str, int] | None = None):
        self.caps = dict(
            tool_input=CAP_TOOL_INPUT,
            tool_output=CAP_TOOL_OUTPUT,
            file_change=CAP_FILE_CHANGE,
            unknown=CAP_UNKNOWN,
            assistant=CAP_ASSISTANT,
            lifecycle_msg=CAP_LIFECYCLE_MSG,
            compact_msg=CAP_COMPACT_MSG,
        )
        if caps:
            self.caps.update(caps)
        self.reset()

    # -- state ---------------------------------------------------------------

    def reset(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.seq = 0
        self.meta: dict[str, Any] = dict(
            session_id=None, cwd=None, model=None, model_provider=None,
            originator=None, started_at=None, last_timestamp=None,
            source_rollout=None, cli_version=None, source=None,
            thread_source=None, history_mode=None, turn_count=None, tool_names=[],
        )
        self.stats: dict[str, Any] = dict(
            source_rollout=None,
            raw_bytes=0, clean_bytes=0, compression_ratio=0.0,
            raw_records=0, malformed_lines=0, clean_events=0,
            user_messages=0, assistant_messages=0, tool_calls=0, tool_outputs=0,
            file_changes=0, lifecycle_events=0, turn_contexts=0, compactions=0,
            rollbacks=0, world_state_markers=0, unknown_records=0,
            client_context_messages=0, client_blocks_stripped=0,
            reasoning_removed=0, token_events_removed=0, duplicates_removed=0,
            bookkeeping_removed=0, developer_messages_removed=0,
            tool_inputs_parsed=0, tool_inputs_unparsed=0,
            legacy_eventmsg_messages=0, legacy_duplicates_removed=0,
            truncated_events=0,
            events_by_kind={}, unknown_by_source={},
        )
        # call_id -> tool name (two-pass pairing: chronological emit + backfill)
        self.call_tool: dict[str, str] = {}
        # rollback bookkeeping: turn segments. A segment is bounded by
        # turn_context records (turn_id) with user-boundary fallback when no
        # turn_context exists; each user turn = one segment.
        self.segments: list[dict[str, Any]] = []
        self.current_segment: dict[str, Any] = {"turn_id": None, "events": [], "has_user": False}
        self.last_user_seg: dict[str, Any] | None = None  # legacy-duplicate detection
        self.last_user_text: str | None = None
        self.model_first: str | None = None
        self.last_timestamp: str | None = None
        self.tool_names: set[str] = set()

    # -- helpers ---------------------------------------------------------------

    def count(self, key: str, n: int = 1) -> None:
        self.stats[key] += n

    def count_unknown(self, source_type: str, raw_type: str) -> None:
        key = f"{source_type}/{raw_type}"
        self.stats["unknown_by_source"][key] = self.stats["unknown_by_source"].get(key, 0) + 1

    def trunc(self, text: str, cap: str, line: int) -> str:
        """truncate_head_tail wrapper that also counts truncated events."""
        out = truncate_head_tail(text, self.caps[cap], line)
        if out != text:
            self.count("truncated_events")
        return out

    def emit(self, kind: str, source_line: int, source_type: str,
             source_ordinal: Any, timestamp: str | None, **fields: Any) -> dict[str, Any]:
        self.seq += 1
        ev: dict[str, Any] = {
            "seq": self.seq,
            "kind": kind,
            "source_line": source_line,
            "source_type": source_type,
        }
        if source_ordinal is not None:
            ev["source_ordinal"] = source_ordinal
        if timestamp:
            ev["timestamp"] = timestamp
        ev.update({k: v for k, v in fields.items()
                   if v is not None or k in ("command", "parse_status")})
        self.events.append(ev)
        self.stats["events_by_kind"][kind] = self.stats["events_by_kind"].get(kind, 0) + 1
        # every event belongs to the current turn segment (rollback unit)
        self.current_segment["events"].append(self.seq)
        return ev

    def start_turn_segment(self, turn_id: str | None) -> None:
        """A turn_context record opens a new turn segment."""
        if self.current_segment["events"]:
            self.segments.append(self.current_segment)
        self.current_segment = {"turn_id": turn_id, "events": [], "has_user": False}

    def user_boundary(self) -> None:
        """A user message opens/occupies a user-turn segment.

        With a known turn_id the message joins the current segment (multiple
        user messages within one turn are ONE rollback unit, matching Codex
        drop_last_n_user_turns). Without any turn context (fallback), each user
        message becomes its own boundary segment.
        """
        seg = self.current_segment
        if seg["turn_id"] is None and seg["events"]:
            self.segments.append(seg)
            self.current_segment = {"turn_id": None, "events": [], "has_user": False}
        self.current_segment["has_user"] = True

    def content_items_text(self, items: Any, is_user: bool, line: int) -> str:
        """Flatten message content items to text (client-block stripping and
        truncation counting delegated to the shared helper)."""
        return content_items_text(items, is_user, line, self.caps["unknown"], self.stats)

    # -- dispatch ---------------------------------------------------------------

    def handle_record(self, line_no: int, raw: dict[str, Any]) -> None:
        top_type = raw.get("type")
        timestamp = raw.get("timestamp") if isinstance(raw.get("timestamp"), str) else None
        if timestamp:
            self.last_timestamp = timestamp
        ordinal = raw.get("ordinal")
        payload = raw.get("payload")

        if not isinstance(top_type, str) or top_type not in KNOWN_TOP_TYPES:
            self.count("unknown_records")
            self.count_unknown("top-level", str(top_type))
            self.emit("unknown", line_no, str(top_type), ordinal, timestamp,
                      raw_type=str(top_type),
                      raw_payload=self.trunc(json.dumps(payload, ensure_ascii=False),
                                             "unknown", line_no))
            return

        if top_type == "session_meta":
            self.handle_session_meta(payload)
        elif top_type == "event_msg":
            self.handle_event_msg(line_no, ordinal, timestamp, payload)
        elif top_type == "response_item":
            self.handle_response_item(line_no, ordinal, timestamp, payload)
        elif top_type == "turn_context":
            self.handle_turn_context(line_no, ordinal, timestamp, payload)
        elif top_type == "world_state":
            self.handle_world_state(line_no, ordinal, timestamp, payload)
        elif top_type == "compacted":
            self.handle_compacted(line_no, ordinal, timestamp, payload, "compacted")

    # -- handlers ---------------------------------------------------------------

    def handle_session_meta(self, payload: Any) -> None:
        common = extract_session_meta(payload)
        self.meta.update(common)

    def handle_turn_context(self, line_no: int, ordinal: Any, timestamp: str | None,
                            payload: Any) -> None:
        self.count("turn_contexts")
        p = payload if isinstance(payload, dict) else {}
        fields: dict[str, Any] = {}
        for k in ("turn_id", "model", "cwd", "workspace_roots", "approval_policy",
                  "current_date", "timezone"):
            v = p.get(k)
            if v is not None:
                fields[k] = v
        sandbox = p.get("sandbox_policy")
        if isinstance(sandbox, dict):
            fields["sandbox_policy"] = sandbox.get("type")
        elif isinstance(sandbox, str):
            fields["sandbox_policy"] = sandbox
        if self.model_first is None and isinstance(p.get("model"), str):
            self.model_first = p["model"]
        self.emit("turn_context", line_no, "turn_context", ordinal, timestamp, **fields)
        if isinstance(p.get("turn_id"), str):
            self.start_turn_segment(p["turn_id"])

    def handle_world_state(self, line_no: int, ordinal: Any, timestamp: str | None,
                           payload: Any) -> None:
        """STRIP payload, KEEP a traceable marker (world_state participates in
        native Codex resume reconstruction, so it must never be silent-ignored)."""
        self.count("world_state_markers")
        p = payload if isinstance(payload, dict) else {}
        full = p.get("full")
        state = p.get("state")
        state_keys = sorted(state.keys()) if isinstance(state, dict) else None
        raw_json = json.dumps(p, ensure_ascii=False)
        raw_bytes = len(raw_json.encode("utf-8"))
        digest = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        self.emit("world_state_marker", line_no, "world_state", ordinal, timestamp,
                  full=full if isinstance(full, bool) else None,
                  state_keys=state_keys,
                  payload_bytes=raw_bytes,
                  sha256=digest,
                  payload_omitted=True)

    def handle_compacted(self, line_no: int, ordinal: Any, timestamp: str | None,
                         payload: Any, source_type: str = "compacted") -> None:
        """KEEP a lightweight marker; do NOT re-inject full replacement_history."""
        self.count("compactions")
        p = payload if isinstance(payload, dict) else {}
        message = p.get("message") if isinstance(p.get("message"), str) else None
        if message:
            message = self.trunc(message, "compact_msg", line_no)
        rh = p.get("replacement_history")
        rh_items = len(rh) if isinstance(rh, list) else None
        rh_bytes = len(json.dumps(rh, ensure_ascii=False).encode("utf-8")) if isinstance(rh, list) else 0
        self.emit("compaction", line_no, source_type, ordinal, timestamp,
                  message=message,
                  window_number=p.get("window_number"),
                  window_id=p.get("window_id"),
                  first_window_id=p.get("first_window_id"),
                  previous_window_id=p.get("previous_window_id"),
                  replacement_history_present=isinstance(rh, list),
                  replacement_history_items=rh_items,
                  replacement_history_bytes=rh_bytes)

    def handle_event_msg(self, line_no: int, ordinal: Any, timestamp: str | None,
                         payload: Any) -> None:
        if not isinstance(payload, dict):
            self.emit_unknown(line_no, ordinal, timestamp, "event_msg", "event_msg", payload)
            return
        ptype = payload.get("type")
        if not isinstance(ptype, str) or ptype not in KNOWN_EVENT_MSG_TYPES:
            self.emit_unknown(line_no, ordinal, timestamp, "event_msg", ptype, payload)
            return

        if ptype == "item_completed":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == FILE_CHANGE_TYPE:
                self.handle_file_change(line_no, ordinal, timestamp, payload)
                return
            if isinstance(item, dict) and item.get("type") in ITEM_COMPLETED_DUPLICATE_TYPES:
                self.count("duplicates_removed")
                return
            # unknown item type (or non-dict item): fail-visible, never silent-drop
            if isinstance(item, dict):
                raw_type = f"item_completed:{item.get('type')}"
            elif item is None:
                raw_type = "item_completed:<missing-item>"
            else:
                raw_type = f"item_completed:<{type(item).__name__}>"
            self.count("unknown_records")
            self.count_unknown("event_msg", raw_type)
            self.emit("unknown", line_no, "event_msg", ordinal, timestamp,
                      raw_type=raw_type,
                      raw_payload=self.trunc(json.dumps(payload, ensure_ascii=False),
                                             "unknown", line_no))
            return
        if ptype in ("token_count",):
            self.count("token_events_removed")
            return
        if ptype in ("reasoning", "agent_reasoning"):
            self.count("reasoning_removed")
            return
        if ptype in ("task_started", "thread_settings_applied"):
            self.count("bookkeeping_removed")
            return
        if ptype in LIFECYCLE_EVENTS:
            self.count("lifecycle_events")
            fields: dict[str, Any] = {"event": ptype}
            for k in ("turn_id", "started_at", "completed_at", "duration_ms",
                      "reason", "error"):
                v = payload.get(k)
                if v is not None:
                    fields[k] = v
            msg = payload.get("message")
            if not isinstance(msg, str):
                msg = payload.get("last_agent_message")
            if isinstance(msg, str) and msg:
                fields["message"] = self.trunc(msg, "lifecycle_msg", line_no)
            self.emit("lifecycle", line_no, "event_msg", ordinal, timestamp, **fields)
            return
        if ptype == "thread_rolled_back":
            self.handle_rollback(line_no, ordinal, timestamp, payload)
            return
        if ptype == "compacted":  # defensive: some builds nest it under event_msg
            self.handle_compacted(line_no, ordinal, timestamp, payload, "event_msg")
            return
        if ptype in ("user_message", "agent_message", "assistant_message"):
            self.handle_legacy_message(line_no, ordinal, timestamp, ptype, payload)
            return
        if ptype == "web_search_end":
            # completion marker of a web_search_call; pairs via call_id.
            # The call record may carry the id under "id" instead of "call_id".
            call_id = payload.get("call_id")
            query = payload.get("query")
            if not isinstance(query, str) or not query:
                action = payload.get("action")
                if isinstance(action, dict):
                    qs = action.get("queries")
                    if isinstance(qs, list) and qs:
                        query = str(qs[0])
            tool = self.call_tool.get(call_id) if isinstance(call_id, str) else None
            self.emit("search_end", line_no, "event_msg", ordinal, timestamp,
                      call_id=call_id,
                      tool=tool if tool is not None else "web_search_call",
                      query=truncate_head_tail(query or "", 500, line_no))
            return

    def handle_rollback(self, line_no: int, ordinal: Any, timestamp: str | None,
                        payload: dict) -> None:
        """Apply Codex rollback semantics (drop_last_n_user_turns): the newest N
        still-active user-turn segments become inactive; everything else survives.

        Segments are turn-aware: bounded by turn_context.turn_id when available,
        so multiple user messages within one turn form a SINGLE rollback unit
        (matching Codex). When no turn context exists the segment is
        user-boundary based and the rollback event reports
        confidence="approximate" instead of pretending to be exact.
        """
        self.count("rollbacks")
        try:
            num_turns = int(payload.get("num_turns"))
        except (TypeError, ValueError):
            num_turns = None
        candidate = list(self.segments)
        if self.current_segment["events"]:
            candidate.append(self.current_segment)
        invalidated_users = 0
        invalidated_events = 0
        budget = num_turns if num_turns is not None else 0
        checked_exact = True
        for seg in reversed(candidate):
            if budget <= 0:
                break
            if not seg["has_user"]:
                continue  # not a user turn; does not consume the budget
            if seg["turn_id"] is None:
                checked_exact = False
            has_active = any(
                self.events[s - 1].get("active") is not False and
                self.events[s - 1]["kind"] != "rollback"
                for s in seg["events"]
            )
            if not has_active:
                continue  # already rolled back; does not consume the budget
            budget -= 1
            invalidated_users += 1
            for s in seg["events"]:
                ev = self.events[s - 1]
                if ev["kind"] == "rollback":
                    continue
                if ev.get("active") is not False:
                    ev["active"] = False
                    ev["invalidated_by"] = "thread_rolled_back"
                    invalidated_events += 1
        self.emit("rollback", line_no, "event_msg", ordinal, timestamp,
                  num_turns=num_turns,
                  invalidated_users=invalidated_users,
                  invalidated_events=invalidated_events,
                  confidence=("exact" if (checked_exact and num_turns is not None) else "approximate"))

    def handle_legacy_message(self, line_no: int, ordinal: Any, timestamp: str | None,
                              ptype: str, payload: dict) -> None:
        self.count("legacy_eventmsg_messages")
        msg = payload.get("message")
        text = msg if isinstance(msg, str) else ""
        if ptype == "user_message":
            text = text.strip()
            if not text:
                self.count("client_context_messages")
                self.emit("client_context", line_no, "event_msg", ordinal, timestamp,
                          id=payload.get("id"), via="event_msg")
                return
            # legacy codex-tui rollouts write BOTH a response_item user message
            # and an event_msg/user_message marker for the same input; within one
            # turn segment an identical legacy text is a duplicate marker
            if (self.last_user_seg is self.current_segment
                    and text == self.last_user_text):
                self.count("legacy_duplicates_removed")
                return
            self.count("user_messages")
            self.user_boundary()
            self.emit("user", line_no, "event_msg", ordinal, timestamp,
                      id=payload.get("id"), active=True, via="event_msg", text=text)
            self.last_user_seg = self.current_segment
            self.last_user_text = text
        else:
            if not text:
                self.count("assistant_messages")
                self.emit("assistant", line_no, "event_msg", ordinal, timestamp,
                          id=payload.get("id"), phase=None, active=True,
                          via="event_msg", text="")
                return
            self.count("assistant_messages")
            self.emit("assistant", line_no, "event_msg", ordinal, timestamp,
                      id=payload.get("id"), phase=None, active=True,
                      via="event_msg", text=self.trunc(text, "assistant", line_no))

    def handle_response_item(self, line_no: int, ordinal: Any, timestamp: str | None,
                             payload: Any) -> None:
        if not isinstance(payload, dict):
            self.emit_unknown(line_no, ordinal, timestamp, "response_item", "response_item", payload)
            return
        ptype = payload.get("type")
        if not isinstance(ptype, str) or ptype not in KNOWN_RESPONSE_ITEM_TYPES:
            self.emit_unknown(line_no, ordinal, timestamp, "response_item", ptype, payload)
            return

        if ptype == "message":
            role = payload.get("role")
            if role == "user":
                self.handle_user_message(line_no, ordinal, timestamp, payload)
            elif role == "assistant":
                self.handle_assistant_message(line_no, ordinal, timestamp, payload)
            elif role == "developer":
                self.count("developer_messages_removed")
            else:
                self.emit_unknown(line_no, ordinal, timestamp, "response_item",
                                  f"message:{role}", payload)
            return
        if ptype == "reasoning":
            self.count("reasoning_removed")
            return
        if ptype == "custom_tool_call":
            self.handle_tool_call(line_no, ordinal, timestamp, payload, False)
            return
        if ptype == "function_call":
            self.handle_tool_call(line_no, ordinal, timestamp, payload, True)
            return
        if ptype in ("custom_tool_call_output", "function_call_output"):
            self.handle_tool_output(line_no, ordinal, timestamp, payload)
            return
        if ptype == "web_search_call":
            self.count("tool_calls")
            action = payload.get("action")
            self.tool_names.add("web_search_call")
            # real legacy samples put the id in "id"; accept both forms
            call_id = payload.get("call_id") or payload.get("id")
            if isinstance(call_id, str):
                self.call_tool[call_id] = "web_search_call"
            self.emit("tool_call", line_no, "response_item", ordinal, timestamp,
                      id=payload.get("id"), call_id=call_id,
                      tool="web_search_call", status=payload.get("status"),
                      parse_status=None,
                      input=self.trunc(json.dumps(action, ensure_ascii=False),
                                       "tool_input", line_no)
                      if action is not None else None)
            return

    def handle_user_message(self, line_no: int, ordinal: Any, timestamp: str | None,
                            payload: dict) -> None:
        text = self.content_items_text(payload.get("content"), is_user=True, line=line_no)
        text = text.strip()
        if not text:
            # the message was 100% client-injected boilerplate
            self.count("client_context_messages")
            self.emit("client_context", line_no, "response_item", ordinal, timestamp,
                      id=payload.get("id"))
            return
        self.count("user_messages")
        self.user_boundary()
        self.emit("user", line_no, "response_item", ordinal, timestamp,
                  id=payload.get("id"), active=True, text=text)
        self.last_user_seg = self.current_segment
        self.last_user_text = text

    def handle_assistant_message(self, line_no: int, ordinal: Any, timestamp: str | None,
                                 payload: dict) -> None:
        self.count("assistant_messages")
        text = self.content_items_text(payload.get("content"), is_user=False, line=line_no)
        phase = payload.get("phase") if isinstance(payload.get("phase"), str) else None
        self.emit("assistant", line_no, "response_item", ordinal, timestamp,
                  id=payload.get("id"), phase=phase, active=True,
                  text=self.trunc(text, "assistant", line_no))

    def handle_tool_call(self, line_no: int, ordinal: Any, timestamp: str | None,
                         payload: dict, is_function_call: bool) -> None:
        self.count("tool_calls")
        call_id = payload.get("call_id")
        name = payload.get("name")
        if isinstance(name, str):
            self.tool_names.add(name)
        if isinstance(call_id, str):
            self.call_tool[call_id] = name if isinstance(name, str) else "unknown"

        if is_function_call:
            arguments = payload.get("arguments")
            raw_input = arguments if isinstance(arguments, str) else ""
            command = None
            parse_status = "unparsed"
            if raw_input:
                try:
                    args = json.loads(raw_input)
                    if isinstance(args, dict):
                        parse_status = "parsed"
                        cmd = args.get("cmd") or args.get("command")
                        if isinstance(cmd, str):
                            command = cmd
                except json.JSONDecodeError:
                    pass
            if parse_status == "parsed":
                self.count("tool_inputs_parsed")
            else:
                self.count("tool_inputs_unparsed")
            self.emit("tool_call", line_no, "response_item", ordinal, timestamp,
                      id=payload.get("id"), call_id=call_id, tool=name,
                      namespace=payload.get("namespace"), status=None,
                      command=command, parse_status=parse_status,
                      input=self.trunc(raw_input, "tool_input", line_no))
            return

        raw_input = payload.get("input") if isinstance(payload.get("input"), str) else ""
        command = None
        parse_status = "unparsed"
        if raw_input:
            command, parse_status = extract_exec_command(raw_input)
        if parse_status == "parsed":
            self.count("tool_inputs_parsed")
        else:
            self.count("tool_inputs_unparsed")
        status = payload.get("status") if isinstance(payload.get("status"), str) else None
        self.emit("tool_call", line_no, "response_item", ordinal, timestamp,
                  id=payload.get("id"), call_id=call_id, tool=name, status=status,
                  command=command, parse_status=parse_status,
                  input=self.trunc(raw_input, "tool_input", line_no))

    def handle_tool_output(self, line_no: int, ordinal: Any, timestamp: str | None,
                           payload: dict) -> None:
        self.count("tool_outputs")
        call_id = payload.get("call_id")
        output = payload.get("output")
        if isinstance(output, str):
            text = output
        elif isinstance(output, list):
            text = self.content_items_text(output, is_user=False, line=line_no)
        elif output is None:
            text = ""
        else:
            text = json.dumps(output, ensure_ascii=False)
        tool = self.call_tool.get(call_id) if isinstance(call_id, str) else None
        self.emit("tool_output", line_no, "response_item", ordinal, timestamp,
                  id=payload.get("id"), call_id=call_id, tool=tool,
                  output=self.trunc(text, "tool_output", line_no))

    def handle_file_change(self, line_no: int, ordinal: Any, timestamp: str | None,
                           payload: dict) -> None:
        """FileChange exists ONLY inside event_msg/item_completed (verified against
        real rollouts: response_item has no equivalent), so it is evidence, not a
        duplicate. Content per path entry is capped with an explicit marker."""
        self.count("file_changes")
        truncated = False
        item = payload.get("item")
        changes = item.get("changes") if isinstance(item, dict) else None
        trimmed: Any = None
        if isinstance(changes, dict):
            trimmed = {}
            for path, info in changes.items():
                if isinstance(info, dict):
                    out: dict[str, Any] = {}
                    for k, v in info.items():
                        if isinstance(v, str):
                            capped = self.trunc(v, "file_change", line_no)
                            out[k] = capped
                            if capped != v:
                                truncated = True
                        else:
                            out[k] = v
                    trimmed[path] = out
                else:
                    trimmed[path] = info
        elif isinstance(changes, str):
            trimmed = self.trunc(changes, "file_change", line_no)
            truncated = trimmed != changes
        fields: dict[str, Any] = {}
        if isinstance(item, dict) and item.get("id") is not None:
            fields["item_id"] = item["id"]
        if trimmed is not None:
            fields["changes"] = trimmed
        status = item.get("status") if isinstance(item, dict) else None
        if isinstance(status, str) and status:
            fields["status"] = status
        if truncated:
            fields["truncated"] = True
        self.emit("file_change", line_no, "event_msg", ordinal, timestamp, **fields)

    def emit_unknown(self, line_no: int, ordinal: Any, timestamp: str | None,
                     source_type: str, raw_type: str, payload: Any) -> None:
        self.count("unknown_records")
        self.count_unknown(source_type, raw_type)
        self.emit("unknown", line_no, source_type, ordinal, timestamp,
                  raw_type=raw_type,
                  raw_payload=self.trunc(json.dumps(payload, ensure_ascii=False),
                                         "unknown", line_no))

    # -- run -------------------------------------------------------------------

    def run(self, source_path: Path, out_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        self.reset()
        source_path = Path(source_path)
        if not source_path.is_file():
            raise SystemExit(f"error: input file not found: {source_path}")
        self.stats["raw_bytes"] = source_path.stat().st_size

        # streaming read: works for very large rollouts without loading them whole
        with source_path.open("r", encoding="utf-8-sig", errors="strict", newline="") as f:
            for idx, line in enumerate(f, start=1):
                line = line.rstrip("\r\n")
                if not line:
                    self.stats["raw_records"] += 1
                    continue
                self.stats["raw_records"] += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    self.stats["malformed_lines"] += 1
                    self.count("unknown_records")
                    self.count_unknown("raw-line", "malformed-json")
                    self.emit("unknown", idx, "raw-line", None, None,
                              raw_type="malformed-json", raw_text=line[: self.caps["unknown"]])
                    continue
                if not isinstance(rec, dict):
                    self.stats["malformed_lines"] += 1
                    self.count("unknown_records")
                    self.count_unknown("raw-line", "non-object")
                    self.emit("unknown", idx, "raw-line", None, None,
                              raw_type="non-object", raw_text=line[: self.caps["unknown"]])
                    continue
                self.handle_record(idx, rec)

        # close the final turn segment
        if self.current_segment["events"]:
            self.segments.append(self.current_segment)
            self.current_segment = {"turn_id": None, "events": [], "has_user": False}

        # two-pass pairing backfill: outputs whose call appeared earlier are
        # already paired chronologically; anything still unpaired gets its tool
        # name from the full call_id -> tool map (robust to unusual ordering)
        if self.call_tool:
            for ev in self.events:
                if ev["kind"] == "tool_output" and ev.get("tool") is None \
                        and isinstance(ev.get("call_id"), str):
                    ev["tool"] = self.call_tool.get(ev["call_id"])

        self.stats["clean_events"] = len(self.events)
        self.stats["clean_bytes"] = sum(
            len(self.serialize_event(ev).encode("utf-8")) + 1 for ev in self.events
        )
        self.stats["compression_ratio"] = (
            round(self.stats["raw_bytes"] / self.stats["clean_bytes"], 2)
            if self.stats["clean_bytes"] else 0.0
        )
        self.meta["last_timestamp"] = self.last_timestamp
        self.meta["model"] = self.model_first
        self.meta["turn_count"] = self.stats["turn_contexts"]
        self.meta["tool_names"] = sorted(self.tool_names)
        self.meta["source_rollout"] = source_path.name
        self.stats["source_rollout"] = source_path.name

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "clean.jsonl").open("w", encoding="utf-8", newline="\n") as f:
            for ev in self.events:
                f.write(self.serialize_event(ev) + "\n")
        with (out_dir / "meta.json").open("w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(self.meta, ensure_ascii=False, indent=2) + "\n")
        with (out_dir / "stats.json").open("w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(self.stats, ensure_ascii=False, indent=2) + "\n")
        return self.meta, self.stats

    @staticmethod
    def serialize_event(ev: dict[str, Any]) -> str:
        return json.dumps(ev, ensure_ascii=False, separators=(",", ":"))


def extract_exec_command(input_text: str) -> tuple[str | None, str]:
    """Deterministically extract the JSON command object wrapped in a JS tool call.

    Looks for `exec_command(` and scans the first balanced {...} object after it,
    then parses with json.loads only. NEVER evaluates or executes JS. On any parse
    problem the caller keeps the raw input untouched with parse_status "unparsed".
    """
    idx = input_text.find("exec_command(")
    if idx == -1:
        return None, "unparsed"
    i = idx + len("exec_command(")
    depth = 0
    in_str = False
    esc = False
    j = i
    while j < len(input_text):
        ch = input_text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
        j += 1
    if depth != 0:
        return None, "unparsed"
    try:
        obj = json.loads(input_text[i : j + 1])
    except json.JSONDecodeError:
        return None, "unparsed"
    if not isinstance(obj, dict):
        return None, "unparsed"
    cmd = obj.get("cmd")
    if not isinstance(cmd, str):
        cmd = obj.get("command")
    if isinstance(cmd, str) and cmd:
        return cmd, "parsed"
    return None, "unparsed"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Codex rollout -> clean session representation (deterministic).")
    ap.add_argument("--input", required=True, help="path to rollout-*.jsonl (read-only)")
    ap.add_argument("--output", required=True, help="output directory (created if missing)")
    ap.add_argument("--cap-tool-input", type=int, default=CAP_TOOL_INPUT)
    ap.add_argument("--cap-tool-output", type=int, default=CAP_TOOL_OUTPUT)
    ap.add_argument("--cap-file-change", type=int, default=CAP_FILE_CHANGE)
    ap.add_argument("--cap-unknown", type=int, default=CAP_UNKNOWN)
    args = ap.parse_args(argv)

    caps = dict(
        tool_input=args.cap_tool_input,
        tool_output=args.cap_tool_output,
        file_change=args.cap_file_change,
        unknown=args.cap_unknown,
    )
    pre = Preprocessor(caps)
    meta, stats = pre.run(Path(args.input), Path(args.output))
    print(f"input:  {args.input}")
    print(f"output: {Path(args.output) / 'clean.jsonl'}")
    print(
        f"records={stats['raw_records']} events={stats['clean_events']} "
        f"users={stats['user_messages']} assistants={stats['assistant_messages']} "
        f"tools={stats['tool_calls']}/{stats['tool_outputs']} "
        f"file_changes={stats['file_changes']} lifecycle={stats['lifecycle_events']} "
        f"unknown={stats['unknown_records']} malformed={stats['malformed_lines']}"
    )
    print(
        f"removed: reasoning={stats['reasoning_removed']} tokens={stats['token_events_removed']} "
        f"duplicates={stats['duplicates_removed']} bookkeeping={stats['bookkeeping_removed']} "
        f"developer={stats['developer_messages_removed']} "
        f"client_blocks={stats['client_blocks_stripped']}"
    )
    print(
        f"bytes: raw={stats['raw_bytes']} clean={stats['clean_bytes']} "
        f"ratio={stats['compression_ratio']}x"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())