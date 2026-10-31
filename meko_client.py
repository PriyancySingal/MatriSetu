import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
import threading
import time
from typing import Any, Optional

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import config

logger = logging.getLogger(__name__)

_TRIAGE_LEVELS = {"GREEN": 0, "AMBER": 1, "RED": 2}
_TRIAGE_RECORD_PREFIX = "MATRISETU_TRIAGE_V1 "
_TRIAGE_RECORD_PATTERN = re.compile(r"MATRISETU_TRIAGE_V1 (\{[^\r\n]*\})")
_MEKO_AGENT_ID = "matrisetu"
_MEKO_READ_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="meko-read")
_MEKO_WRITE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meko-write")
_MEKO_EVENT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meko-events")
_CONVERSATION_CACHE: dict[str, tuple[str, str]] = {}
_TRIAGE_CACHE: dict[str, tuple[Optional[str], datetime, datetime]] = {}
_TRIAGE_CACHE_LOCK = threading.Lock()
_IN_FLIGHT_LOOKUPS: set[str] = set()
_PANEL_CACHE: dict[str, dict[str, Any]] = {}
_PANEL_CACHE_LOCK = threading.Lock()
_PANEL_LOOKUPS: set[str] = set()
_CACHE_EPOCH: dict[str, int] = {}
_LOOKUP_REFRESH_INTERVAL = timedelta(minutes=5)
_PANEL_REFRESH_INTERVAL = timedelta(seconds=15)


def _exception_type_names(error: BaseException) -> list[str]:
    nested_errors = getattr(error, "exceptions", ())
    if not nested_errors:
        return [type(error).__name__]
    return [
        name
        for nested in nested_errors
        for name in _exception_type_names(nested)
    ]


def patient_id(identifier: str) -> str:
    """Create a pseudonymous patient identifier."""
    if not config.MEKO_PATIENT_SALT:
        raise ValueError("MEKO_PATIENT_SALT is not configured")

    digest = hashlib.sha256(
        f"{config.MEKO_PATIENT_SALT}:{identifier}".encode()
    ).hexdigest()

    return f"P-{digest[:16]}"


def _extract_id(result: Any, preferred_key: str) -> Optional[str]:
    """Extract an ID from an MCP result."""
    if isinstance(result, list):
        for item in result:
            found_id = _extract_id(item, preferred_key)
            if found_id:
                return found_id

    if isinstance(result, dict):
        if result.get(preferred_key):
            return str(result[preferred_key])
        if result.get("id"):
            return str(result["id"])
        for key in ("result", "data", "output", "results"):
            if result.get(key) is not None:
                nested_id = _extract_id(result[key], preferred_key)
                if nested_id:
                    return nested_id

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            return _extract_id(parsed, preferred_key)
        except (json.JSONDecodeError, TypeError):
            pass

    return None


async def _call_meko_async(tool_name: str, arguments: dict) -> Any:
    """Connect to Meko and invoke one MCP tool."""
    headers = {"Authorization": f"Bearer {config.MEKO_API_KEY}"}

    async with httpx2.AsyncClient(
        headers=headers,
        timeout=config.MEKO_TIMEOUT_SECONDS,
    ) as client:
        async with streamable_http_client(
            config.MEKO_MCP_URL,
            http_client=client,
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.call_tool(tool_name, arguments)


def _safe_call(tool_name: str, arguments: dict) -> Any:
    """Call Meko safely; never interrupt MatriSetu if Meko fails."""
    if not config.MEKO_ENABLED or not config.MEKO_API_KEY:
        return None

    try:
        result = asyncio.run(
            asyncio.wait_for(
                _call_meko_async(tool_name, arguments),
                timeout=config.MEKO_TIMEOUT_SECONDS,
            )
        )
    except Exception as exc:
        error_types = ", ".join(_exception_type_names(exc))
        logger.warning("Meko call failed (%s): %s", tool_name, error_types)
        return None

    if getattr(result, "is_error", False):
        logger.warning("Meko call failed (%s): MCP tool returned an error", tool_name)
        return None

    return result


def _result_text(result: Any) -> str:
    """Convert MCP tool output into readable text."""
    if result is None:
        return ""

    structured_content = getattr(result, "structured_content", None)
    if structured_content is None:
        structured_content = getattr(result, "structuredContent", None)
    if structured_content is not None:
        return json.dumps(structured_content, ensure_ascii=False)

    content = getattr(result, "content", None)
    if content:
        parts = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts)

    return str(result)


def _write_meko_event(event: dict) -> None:
    try:
        from database import log_meko_event

        log_meko_event(**event)
    except Exception as exc:
        logger.warning("Meko event logging failed (%s)", type(exc).__name__)


def _log_meko_event_async(
    identifier: str,
    asha_id: Optional[str],
    event_type: str,
    ok: bool,
    latency_ms: Optional[int] = None,
    level_ai: Optional[str] = None,
    level_memory: Optional[str] = None,
    level_final: Optional[str] = None,
    memory_id: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    try:
        hashed_patient_id = patient_id(identifier)
    except ValueError:
        return

    event = {
        "phone": identifier,
        "patient_hash": hashed_patient_id,
        "asha_id": asha_id,
        "event_type": event_type,
        "level_ai": level_ai,
        "level_memory": level_memory,
        "level_final": level_final,
        "memory_id": memory_id,
        "ok": ok,
        "latency_ms": latency_ms,
        "note": note,
    }
    try:
        _MEKO_EVENT_EXECUTOR.submit(_write_meko_event, event)
    except RuntimeError as exc:
        logger.warning("Meko event queue unavailable (%s)", type(exc).__name__)


def log_triage_decision(
    identifier: str,
    asha_id: Optional[str],
    level_ai: str,
    level_memory: Optional[str],
    level_final: str,
) -> None:
    if not config.MEKO_ENABLED:
        return
    _log_meko_event_async(
        identifier,
        asha_id,
        "recall",
        ok=True,
        level_ai=level_ai,
        level_memory=level_memory,
        level_final=level_final,
        note="triage decision",
    )


def _search_patient_memory(
    conversation_id: str,
    agent_id: str,
    datapack_id: str,
    limit: int = 100,
) -> tuple[str, bool, int]:
    started = time.monotonic()
    result = _safe_call(
        "memory_search",
        {
            "query": _TRIAGE_RECORD_PREFIX.strip(),
            "conversation_id": conversation_id,
            "run_id": conversation_id,
            "agent_id": agent_id,
            "datapack_id": datapack_id,
            "limit": limit,
        },
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    if result is None:
        return "", False, latency_ms
    return _result_text(result), True, latency_ms


def _parse_memory_records(memory_text: str) -> list[dict[str, Any]]:
    entries = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 10:
            return
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return
            if parsed != value:
                visit(parsed, depth + 1)
            return
        if isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
            return
        if not isinstance(value, dict):
            return

        memory = value.get("memory")
        if isinstance(memory, str):
            match = _TRIAGE_RECORD_PATTERN.search(memory)
            if match:
                try:
                    record = json.loads(match.group(1))
                    recorded_at = datetime.fromisoformat(record["recorded_at"])
                    if recorded_at.tzinfo is None:
                        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
                    if (
                        record.get("level") in _TRIAGE_LEVELS
                        and record.get("status") in {"active", "resolved"}
                    ):
                        entries.append({
                            "memory_id": str(value.get("id") or ""),
                            "agent_id": value.get("agent_id"),
                            "run_id": value.get("run_id"),
                            "level": record["level"],
                            "status": record["status"],
                            "week": record.get("week"),
                            "postpartum": bool(record.get("postpartum", False)),
                            "recorded_at": recorded_at,
                            "raw_excerpt": memory[:280],
                            "seeded": "seeded" in memory.lower(),
                        })
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    pass
        for key, item in value.items():
            if key != "memory":
                visit(item, depth + 1)

    visit(memory_text)
    unique = {}
    for entry in entries:
        key = entry["memory_id"] or (
            entry["level"], entry["status"], entry["recorded_at"]
        )
        unique[key] = entry
    return list(unique.values())


def _panel_snapshot_from_search(
    identifier: str,
    asha_id: Optional[str],
) -> dict[str, Any]:
    conversation = _patient_conversation(identifier)
    if not conversation:
        return {"state": "unavailable", "record": None, "records": []}
    conversation_id, agent_id = conversation
    raw, ok, latency_ms = _search_patient_memory(
        conversation_id,
        agent_id,
        config.MEKO_PATIENTS_DATAPACK,
    )
    if not ok:
        _log_meko_event_async(
            identifier, asha_id, "recall", False, latency_ms=latency_ms,
            note="live panel recall failed",
        )
        return {"state": "unavailable", "record": None, "records": []}

    records = [
        row for row in _parse_memory_records(raw)
        if row["agent_id"] == agent_id and row["run_id"] == conversation_id
    ]
    latest = max(records, key=lambda row: row["recorded_at"], default=None)
    now = datetime.now(timezone.utc)
    effective = recent_triage_record(
        "\n".join(row["raw_excerpt"] for row in records),
        now,
    )
    effective_level = effective[0] if effective and effective[1] == "active" else None
    display_record = None
    if latest:
        age_seconds = max(0, int((now - latest["recorded_at"]).total_seconds()))
        display_record = {
            **latest,
            "recorded_at": latest["recorded_at"].isoformat(),
            "age_seconds": age_seconds,
            "effective_level": effective_level,
        }
    _log_meko_event_async(
        identifier,
        asha_id,
        "recall",
        True,
        latency_ms=latency_ms,
        level_memory=effective_level,
        memory_id=latest["memory_id"] if latest else None,
        note="live panel recall",
    )
    return {
        "state": "available" if latest else "empty",
        "record": display_record,
        "records": [
            {
                **row,
                "recorded_at": row["recorded_at"].isoformat(),
                "memory_id": row["memory_id"],
            }
            for row in sorted(records, key=lambda item: item["recorded_at"], reverse=True)
        ],
        "effective_level": effective_level,
        "fetched_at": now.isoformat(),
    }


def create_conversation(
    title: str,
    datapack_id: str,
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Optional[str]:
    arguments = {
        "title": title,
        "datapack_id": datapack_id,
        "agent_id": agent_id or _MEKO_AGENT_ID,
    }
    if session_id:
        arguments["session_id"] = session_id

    result = _safe_call(
        "conversation_create",
        arguments,
    )
    return _extract_id(_result_text(result), "conversation_id")


def recall(
    conversation_id: str,
    message: str,
    patient_datapack: bool = True,
    agent_id: Optional[str] = None,
    patient_identifier: Optional[str] = None,
    asha_id: Optional[str] = None,
) -> str:
    """Retrieve relevant context. Caller must redact identifying information."""
    if not conversation_id or not message:
        return ""

    datapack_id = (
        config.MEKO_PATIENTS_DATAPACK
        if patient_datapack
        else config.MEKO_DISTRICT_DATAPACK
    )

    started = time.monotonic()
    result = _safe_call(
        "memory_search",
        {
            "query": message,
            "conversation_id": conversation_id,
            "run_id": conversation_id,
            "agent_id": agent_id or _MEKO_AGENT_ID,
            "datapack_id": datapack_id,
            "limit": 20,
        },
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    memory_text = _result_text(result)
    if patient_identifier and message == _TRIAGE_RECORD_PREFIX.strip():
        records = [
            row for row in _parse_memory_records(memory_text)
            if row["agent_id"] == (agent_id or _MEKO_AGENT_ID)
            and row["run_id"] == conversation_id
        ]
        latest = max(records, key=lambda row: row["recorded_at"], default=None)
        effective = recent_triage_record(
            "\n".join(row["raw_excerpt"] for row in records)
        )
        _log_meko_event_async(
            patient_identifier,
            asha_id,
            "recall",
            result is not None,
            latency_ms=latency_ms,
            level_memory=(
                effective[0] if effective and effective[1] == "active" else None
            ),
            memory_id=latest["memory_id"] if latest else None,
            note="triage recall" if result is not None else "triage recall failed",
        )
    return memory_text


def remember(
    conversation_id: str,
    text: str,
    datapack_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Optional[str]:
    """Save an approved, de-identified memory."""
    if not conversation_id or not text:
        return None

    result = _safe_call(
        "memory_add",
        {
            "text": text,
            "conversation_id": conversation_id,
            "agent_id": agent_id or _MEKO_AGENT_ID,
            "datapack_id": datapack_id or config.MEKO_PATIENTS_DATAPACK,
        },
    )

    return _extract_id(_result_text(result), "memory_id")


def highest_triage_level(current_level: str, remembered_level: Optional[str]) -> str:
    """Keep clinical triage at least as urgent as the current analyzer result."""
    current_rank = _TRIAGE_LEVELS.get(current_level)
    remembered_rank = _TRIAGE_LEVELS.get(remembered_level or "")
    if current_rank is None or remembered_rank is None:
        return current_level
    return remembered_level if remembered_rank > current_rank else current_level


def recent_triage_record(
    memory_text: str,
    now: Optional[datetime] = None,
) -> Optional[tuple[str, str, datetime]]:
    """Read the latest recent, structured MatriSetu triage record."""
    current_time = now or datetime.now(timezone.utc)
    records = []
    for text in _text_fragments(memory_text):
        for match in _TRIAGE_RECORD_PATTERN.finditer(text):
            try:
                record = json.loads(match.group(1))
                if not isinstance(record, dict):
                    continue
                level = record.get("level")
                status = record.get("status")
                recorded_at = datetime.fromisoformat(record["recorded_at"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue

            if (
                not isinstance(level, str)
                or not isinstance(status, str)
                or level not in _TRIAGE_LEVELS
                or status not in {"active", "resolved"}
            ):
                continue
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(tzinfo=timezone.utc)
            if current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=timezone.utc)

            age = (current_time - recorded_at).total_seconds()
            if age < -300 or age > timedelta(hours=24).total_seconds():
                continue
            records.append((recorded_at, level, status))

    if not records:
        return None

    latest_resolution = max(
        (record for record in records if record[2] == "resolved"),
        key=lambda record: record[0],
        default=None,
    )
    active_records = [
        record for record in records
        if record[2] == "active"
        and (latest_resolution is None or record[0] > latest_resolution[0])
    ]
    if active_records:
        latest_active = max(
            active_records,
            key=lambda record: (_TRIAGE_LEVELS[record[1]], record[0]),
        )
        return latest_active[1], latest_active[2], latest_active[0]
    if latest_resolution:
        return latest_resolution[1], latest_resolution[2], latest_resolution[0]
    return None


def recent_triage_level(
    memory_text: str,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Return only the level of a recent active MatriSetu record."""
    record = recent_triage_record(memory_text, now)
    if not record or record[1] != "active":
        return None
    return record[0]


def _text_fragments(value: Any, depth: int = 0):
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return
        if parsed != value:
            yield from _text_fragments(parsed, depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _text_fragments(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _text_fragments(item, depth + 1)


def _patient_conversation(identifier: str) -> Optional[tuple[str, str]]:
    """Get or create a conversation and isolated memory scope for one patient."""
    if not config.MEKO_ENABLED or not config.MEKO_API_KEY:
        return None
    if not config.MEKO_PATIENTS_DATAPACK or not config.MEKO_PATIENT_SALT:
        logger.warning("Meko patient memory is missing required configuration")
        return None

    try:
        hashed_patient_id = patient_id(identifier)
    except ValueError:
        logger.warning("Meko patient memory is missing required configuration")
        return None

    if hashed_patient_id in _CONVERSATION_CACHE:
        return _CONVERSATION_CACHE[hashed_patient_id]

    agent_id = f"{_MEKO_AGENT_ID}:{hashed_patient_id}"
    conversation_id = create_conversation(
        f"MatriSetu patient {hashed_patient_id}",
        config.MEKO_PATIENTS_DATAPACK,
        session_id=hashed_patient_id,
        agent_id=agent_id,
    )
    if not conversation_id:
        return None
    patient_conversation = (conversation_id, agent_id)
    _CONVERSATION_CACHE[hashed_patient_id] = patient_conversation
    return patient_conversation


def _record_to_panel_row(
    level: str,
    status: str,
    week: int,
    postpartum: bool,
    recorded_at: datetime,
    memory_id: Optional[str],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    effective_level = (
        level
        if status == "active"
        and timedelta(0) <= now - recorded_at <= timedelta(hours=24)
        else None
    )
    return {
        "memory_id": memory_id or "",
        "agent_id": None,
        "run_id": None,
        "level": level,
        "status": status,
        "week": week,
        "postpartum": postpartum,
        "recorded_at": recorded_at.isoformat(),
        "age_seconds": max(0, int((now - recorded_at).total_seconds())),
        "raw_excerpt": (
            _TRIAGE_RECORD_PREFIX
            + json.dumps({
                "level": level,
                "status": status,
                "week": week,
                "postpartum": postpartum,
                "recorded_at": recorded_at.isoformat(timespec="seconds"),
            }, separators=(",", ":"))
        ),
        "seeded": False,
        "effective_level": effective_level,
    }


def _refresh_panel_snapshot(
    identifier: str,
    hashed_patient_id: str,
    asha_id: Optional[str],
    lookup_epoch: int,
) -> None:
    try:
        snapshot = _panel_snapshot_from_search(identifier, asha_id)
    except Exception as exc:
        logger.warning("Meko panel recall failed: %s", type(exc).__name__)
        snapshot = {"state": "unavailable", "record": None, "records": []}
    snapshot.setdefault("fetched_at", datetime.now(timezone.utc).isoformat())
    with _PANEL_CACHE_LOCK:
        if _CACHE_EPOCH.get(hashed_patient_id, 0) == lookup_epoch:
            _PANEL_CACHE[hashed_patient_id] = snapshot
            record = snapshot.get("record")
            now = datetime.now(timezone.utc)
            if record and record.get("effective_level"):
                recorded_at = datetime.fromisoformat(record["recorded_at"])
                _TRIAGE_CACHE[hashed_patient_id] = (
                    record["effective_level"],
                    recorded_at + timedelta(hours=24),
                    now,
                )
            elif snapshot.get("state") == "empty":
                _TRIAGE_CACHE[hashed_patient_id] = (
                    None,
                    now + _LOOKUP_REFRESH_INTERVAL,
                    now,
                )
        _PANEL_LOOKUPS.discard(hashed_patient_id)


def get_patient_memory_snapshot(
    identifier: str,
    asha_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return a cached panel snapshot and refresh it asynchronously when stale."""
    if not config.MEKO_ENABLED:
        return {"state": "off", "record": None, "records": []}
    try:
        hashed_patient_id = patient_id(identifier)
    except ValueError:
        return {"state": "unavailable", "record": None, "records": []}

    now = datetime.now(timezone.utc)
    with _PANEL_CACHE_LOCK:
        snapshot = _PANEL_CACHE.get(hashed_patient_id)
        fetched_at = None
        if snapshot and snapshot.get("fetched_at"):
            try:
                fetched_at = datetime.fromisoformat(snapshot["fetched_at"])
                if fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                fetched_at = None
        stale = not fetched_at or now - fetched_at >= _PANEL_REFRESH_INTERVAL
        if stale and hashed_patient_id not in _PANEL_LOOKUPS:
            _PANEL_LOOKUPS.add(hashed_patient_id)
            epoch = _CACHE_EPOCH.get(hashed_patient_id, 0)
            try:
                _MEKO_READ_EXECUTOR.submit(
                    _refresh_panel_snapshot,
                    identifier,
                    hashed_patient_id,
                    asha_id,
                    epoch,
                )
            except RuntimeError as exc:
                _PANEL_LOOKUPS.discard(hashed_patient_id)
                logger.warning("Meko panel lookup unavailable (%s)", type(exc).__name__)

    if snapshot:
        return snapshot
    return {"state": "loading", "record": None, "records": []}


def _fetch_owned_triage_records(
    identifier: str,
    asha_id: Optional[str],
) -> tuple[Optional[tuple[str, str]], list[dict[str, Any]], int]:
    conversation = _patient_conversation(identifier)
    if not conversation:
        return None, [], 0
    conversation_id, agent_id = conversation
    raw, ok, latency_ms = _search_patient_memory(
        conversation_id,
        agent_id,
        config.MEKO_PATIENTS_DATAPACK,
    )
    if not ok:
        _log_meko_event_async(
            identifier, asha_id, "recall", False, latency_ms=latency_ms,
            note="memory management search failed",
        )
        return conversation, [], latency_ms
    records = [
        row for row in _parse_memory_records(raw)
        if row["agent_id"] == agent_id
        and row["run_id"] == conversation_id
        and row["memory_id"]
    ]
    _log_meko_event_async(
        identifier,
        asha_id,
        "recall",
        True,
        latency_ms=latency_ms,
        level_memory=recent_triage_level(
            "\n".join(row["raw_excerpt"] for row in records)
        ),
        note="memory management search",
    )
    return conversation, records, latency_ms


def correct_patient_triage_memory(
    identifier: str,
    level: str,
    status: str,
    asha_id: Optional[str] = None,
) -> dict[str, Any]:
    if level not in _TRIAGE_LEVELS or status not in {"active", "resolved"}:
        return {"ok": False, "error": "invalid correction"}
    conversation, records, latency_ms = _fetch_owned_triage_records(
        identifier, asha_id
    )
    if not conversation:
        return {"ok": False, "error": "Meko unavailable"}
    conversation_id, agent_id = conversation
    active_records = [row for row in records if row["status"] == "active"]
    target = max(
        active_records,
        key=lambda row: row["recorded_at"],
        default=None,
    )
    if not target:
        return {"ok": False, "error": "No active Meko record found"}

    recorded_at = datetime.now(timezone.utc)
    replacement = {
        "level": level,
        "status": status,
        "week": target["week"],
        "postpartum": target["postpartum"],
        "recorded_at": recorded_at.isoformat(timespec="seconds"),
    }
    text = _TRIAGE_RECORD_PREFIX + json.dumps(
        replacement, separators=(",", ":")
    )
    started = time.monotonic()
    result = _safe_call(
        "memory_update",
        {
            "memory_id": target["memory_id"],
            "text": text,
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "datapack_id": config.MEKO_PATIENTS_DATAPACK,
        },
    )
    latency_ms += int((time.monotonic() - started) * 1000)
    ok = result is not None and not getattr(result, "is_error", False)
    _log_meko_event_async(
        identifier,
        asha_id,
        "correct",
        ok,
        latency_ms=latency_ms,
        level_memory=level,
        memory_id=target["memory_id"],
        note="memory corrected" if ok else "memory correction failed",
    )
    if not ok:
        return {"ok": False, "error": "Meko unavailable"}

    hashed_patient_id = patient_id(identifier)
    row = _record_to_panel_row(
        level,
        status,
        target["week"],
        target["postpartum"],
        recorded_at,
        target["memory_id"],
    )
    with _PANEL_CACHE_LOCK:
        epoch = _CACHE_EPOCH.get(hashed_patient_id, 0) + 1
        _CACHE_EPOCH[hashed_patient_id] = epoch
        _PANEL_CACHE[hashed_patient_id] = {
            "state": "available",
            "record": row,
            "records": [row],
            "effective_level": row["effective_level"],
            "fetched_at": recorded_at.isoformat(),
        }
        _TRIAGE_CACHE[hashed_patient_id] = (
            row["effective_level"],
            (
                recorded_at + timedelta(hours=24)
                if status == "active"
                else recorded_at + _LOOKUP_REFRESH_INTERVAL
            ),
            recorded_at,
        )
    return {"ok": True, "memory_id": target["memory_id"]}


def forget_patient_triage_memory(
    identifier: str,
    asha_id: Optional[str] = None,
) -> dict[str, Any]:
    conversation, records, search_latency = _fetch_owned_triage_records(
        identifier, asha_id
    )
    if not conversation:
        return {"ok": False, "deleted": 0, "error": "Meko unavailable"}
    conversation_id, agent_id = conversation
    deleted = 0
    failed = 0
    total_latency = search_latency
    for record in records:
        started = time.monotonic()
        result = _safe_call(
            "memory_delete_by_id",
            {
                "memory_id": record["memory_id"],
                "conversation_id": conversation_id,
                "agent_id": agent_id,
                "datapack_id": config.MEKO_PATIENTS_DATAPACK,
            },
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        total_latency += latency_ms
        ok = result is not None and not getattr(result, "is_error", False)
        deleted += int(ok)
        failed += int(not ok)
        _log_meko_event_async(
            identifier,
            asha_id,
            "forget",
            ok,
            latency_ms=latency_ms,
            memory_id=record["memory_id"],
            note="memory forgotten" if ok else "memory deletion failed",
        )

    hashed_patient_id = patient_id(identifier)
    with _PANEL_CACHE_LOCK:
        _CACHE_EPOCH[hashed_patient_id] = (
            _CACHE_EPOCH.get(hashed_patient_id, 0) + 1
        )
        _PANEL_CACHE.pop(hashed_patient_id, None)
        _TRIAGE_CACHE.pop(hashed_patient_id, None)
    return {
        "ok": failed == 0,
        "deleted": deleted,
        "failed": failed,
        "latency_ms": total_latency,
        "error": "Meko unavailable" if failed else None,
    }


def _get_triage_memory_record(
    identifier: str,
    asha_id: Optional[str] = None,
) -> Optional[tuple[str, str, datetime]]:
    conversation = _patient_conversation(identifier)
    if not conversation:
        return None
    conversation_id, agent_id = conversation

    memory_text = recall(
        conversation_id,
        _TRIAGE_RECORD_PREFIX.strip(),
        agent_id=agent_id,
        patient_identifier=identifier,
        asha_id=asha_id,
    )
    return recent_triage_record(memory_text)


def get_triage_memory_level(identifier: str) -> Optional[str]:
    """Recall recent de-identified triage state without sending patient text."""
    record = _get_triage_memory_record(identifier)
    if not record or record[1] != "active":
        return None
    return record[0]


def remember_triage(
    identifier: str,
    level: str,
    week: int,
    postpartum: bool = False,
    status: str = "active",
    recorded_at: Optional[datetime] = None,
    asha_id: Optional[str] = None,
) -> Optional[str]:
    """Persist only de-identified triage metadata, never patient-authored text."""
    if level not in _TRIAGE_LEVELS or status not in {"active", "resolved"}:
        return None

    conversation = _patient_conversation(identifier)
    if not conversation:
        return None
    conversation_id, agent_id = conversation

    record = {
        "level": level,
        "status": status,
        "week": week,
        "postpartum": postpartum,
        "recorded_at": (recorded_at or datetime.now(timezone.utc)).isoformat(
            timespec="seconds"
        ),
    }
    started = time.monotonic()
    memory_id = remember(
        conversation_id,
        _TRIAGE_RECORD_PREFIX + json.dumps(record, separators=(",", ":")),
        config.MEKO_PATIENTS_DATAPACK,
        agent_id=agent_id,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    event_type = "resolve" if status == "resolved" else "store"
    _log_meko_event_async(
        identifier,
        asha_id,
        event_type,
        memory_id is not None,
        latency_ms=latency_ms,
        level_memory=level,
        memory_id=memory_id,
        note="triage memory written" if memory_id else "triage memory write failed",
    )
    if memory_id:
        hashed_patient_id = patient_id(identifier)
        row = _record_to_panel_row(
            level,
            status,
            week,
            postpartum,
            recorded_at or datetime.now(timezone.utc),
            memory_id,
        )
        with _PANEL_CACHE_LOCK:
            _CACHE_EPOCH[hashed_patient_id] = (
                _CACHE_EPOCH.get(hashed_patient_id, 0) + 1
            )
            snapshot = _PANEL_CACHE.get(hashed_patient_id, {})
            records = [
                item for item in snapshot.get("records", [])
                if item.get("memory_id") != memory_id
            ]
            records.insert(0, row)
            current = snapshot.get("record")
            latest = row
            if current:
                try:
                    old_time = datetime.fromisoformat(current["recorded_at"])
                    if old_time > datetime.fromisoformat(row["recorded_at"]):
                        latest = current
                except (KeyError, TypeError, ValueError):
                    pass
            now = datetime.now(timezone.utc)
            _PANEL_CACHE[hashed_patient_id] = {
                "state": "available",
                "record": latest,
                "records": records[:100],
                "effective_level": latest.get("effective_level"),
                "fetched_at": now.isoformat(),
            }
    return memory_id


def start_triage_memory_lookup(
    identifier: str,
    asha_id: Optional[str] = None,
) -> Optional[Future]:
    """Refresh Meko memory in the background without delaying patient replies."""
    if not config.MEKO_ENABLED or not config.MEKO_API_KEY:
        return None
    try:
        hashed_patient_id = patient_id(identifier)
    except ValueError:
        return None

    now = datetime.now(timezone.utc)
    with _TRIAGE_CACHE_LOCK:
        cached = _TRIAGE_CACHE.get(hashed_patient_id)
        if cached and now - cached[2] < _LOOKUP_REFRESH_INTERVAL:
            return None
        if hashed_patient_id in _IN_FLIGHT_LOOKUPS:
            return None
        _IN_FLIGHT_LOOKUPS.add(hashed_patient_id)
        lookup_epoch = _CACHE_EPOCH.get(hashed_patient_id, 0)

    future = _MEKO_READ_EXECUTOR.submit(
        _refresh_triage_cache,
        identifier,
        hashed_patient_id,
        now,
        asha_id,
        lookup_epoch,
    )
    future.add_done_callback(
        lambda completed: _finish_triage_lookup(hashed_patient_id, completed)
    )
    return future


def _refresh_triage_cache(
    identifier: str,
    hashed_patient_id: str,
    lookup_started_at: datetime,
    asha_id: Optional[str] = None,
    lookup_epoch: Optional[int] = None,
) -> None:
    try:
        record = _get_triage_memory_record(identifier, asha_id)
    except Exception as exc:
        logger.warning("Meko recall failed: %s", type(exc).__name__)
        return

    fetched_at = datetime.now(timezone.utc)
    level = None
    expires_at = fetched_at + _LOOKUP_REFRESH_INTERVAL
    if record and record[1] == "active":
        level, _, recorded_at = record
        expires_at = recorded_at + timedelta(hours=24)

    with _TRIAGE_CACHE_LOCK:
        if (
            lookup_epoch is not None
            and _CACHE_EPOCH.get(hashed_patient_id, 0) != lookup_epoch
        ):
            return
        current = _TRIAGE_CACHE.get(hashed_patient_id)
        if current and current[2] > lookup_started_at:
            if current[0] and record and record[1] == "active":
                merged_level = highest_triage_level(current[0], record[0])
                if merged_level != current[0]:
                    _TRIAGE_CACHE[hashed_patient_id] = (
                        merged_level,
                        record[2] + timedelta(hours=24),
                        fetched_at,
                    )
            return
        _TRIAGE_CACHE[hashed_patient_id] = (level, expires_at, fetched_at)


def _finish_triage_lookup(hashed_patient_id: str, future: Future) -> None:
    with _TRIAGE_CACHE_LOCK:
        _IN_FLIGHT_LOOKUPS.discard(hashed_patient_id)
    try:
        future.result()
    except Exception as exc:
        logger.warning("Meko recall failed: %s", type(exc).__name__)


def cached_triage_memory_level(identifier: str) -> Optional[str]:
    if not config.MEKO_ENABLED or not config.MEKO_API_KEY:
        return None
    try:
        hashed_patient_id = patient_id(identifier)
    except ValueError:
        return None

    now = datetime.now(timezone.utc)
    with _TRIAGE_CACHE_LOCK:
        cached = _TRIAGE_CACHE.get(hashed_patient_id)
        if not cached:
            return None
        level, expires_at, _ = cached
        if expires_at <= now:
            _TRIAGE_CACHE.pop(hashed_patient_id, None)
            return None
        return level


def _cache_triage_level(
    identifier: str,
    level: str,
    status: str,
    recorded_at: datetime,
) -> Optional[str]:
    try:
        hashed_patient_id = patient_id(identifier)
    except ValueError:
        return None

    now = datetime.now(timezone.utc)
    cached_level = level if status == "active" else None
    expires_at = (
        recorded_at + timedelta(hours=24)
        if status == "active"
        else now + _LOOKUP_REFRESH_INTERVAL
    )
    with _TRIAGE_CACHE_LOCK:
        current = _TRIAGE_CACHE.get(hashed_patient_id)
        if (
            status == "active"
            and current
            and current[0]
            and current[1] > now
        ):
            effective_level = highest_triage_level(current[0], level)
            if effective_level == current[0]:
                expires_at = current[1]
            cached_level = effective_level
        _TRIAGE_CACHE[hashed_patient_id] = (cached_level, expires_at, now)
    return cached_level


def queue_triage_memory_record(
    identifier: str,
    level: str,
    week: int,
    postpartum: bool = False,
    status: str = "active",
    asha_id: Optional[str] = None,
) -> Future:
    """Persist triage metadata without delaying the patient-facing reply."""
    requested_at = datetime.now(timezone.utc)
    stored_level = level
    if level in _TRIAGE_LEVELS and status in {"active", "resolved"}:
        stored_level = _cache_triage_level(
            identifier, level, status, requested_at
        ) or level
    return _MEKO_WRITE_EXECUTOR.submit(
        remember_triage,
        identifier,
        stored_level,
        week,
        postpartum,
        status,
        requested_at,
        asha_id,
    )