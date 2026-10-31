import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
import threading
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
_CONVERSATION_CACHE: dict[str, tuple[str, str]] = {}
_TRIAGE_CACHE: dict[str, tuple[Optional[str], datetime, datetime]] = {}
_TRIAGE_CACHE_LOCK = threading.Lock()
_IN_FLIGHT_LOOKUPS: set[str] = set()
_LOOKUP_REFRESH_INTERVAL = timedelta(minutes=5)


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
) -> str:
    """Retrieve relevant context. Caller must redact identifying information."""
    if not conversation_id or not message:
        return ""

    datapack_id = (
        config.MEKO_PATIENTS_DATAPACK
        if patient_datapack
        else config.MEKO_DISTRICT_DATAPACK
    )

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

    return _result_text(result)


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


def _get_triage_memory_record(
    identifier: str,
) -> Optional[tuple[str, str, datetime]]:
    conversation = _patient_conversation(identifier)
    if not conversation:
        return None
    conversation_id, agent_id = conversation

    memory_text = recall(
        conversation_id,
        _TRIAGE_RECORD_PREFIX.strip(),
        agent_id=agent_id,
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
    return remember(
        conversation_id,
        _TRIAGE_RECORD_PREFIX + json.dumps(record, separators=(",", ":")),
        config.MEKO_PATIENTS_DATAPACK,
        agent_id=agent_id,
    )


def start_triage_memory_lookup(identifier: str) -> Optional[Future]:
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

    future = _MEKO_READ_EXECUTOR.submit(
        _refresh_triage_cache, identifier, hashed_patient_id, now
    )
    future.add_done_callback(
        lambda completed: _finish_triage_lookup(hashed_patient_id, completed)
    )
    return future


def _refresh_triage_cache(
    identifier: str,
    hashed_patient_id: str,
    lookup_started_at: datetime,
) -> None:
    try:
        record = _get_triage_memory_record(identifier)
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
    )