import asyncio
import hashlib
import json
import logging
from typing import Any, Optional

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import config

logger = logging.getLogger(__name__)


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
    if isinstance(result, dict):
        if result.get(preferred_key):
            return str(result[preferred_key])
        if result.get("id"):
            return str(result["id"])

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
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.call_tool(tool_name, arguments)


def _safe_call(tool_name: str, arguments: dict) -> Any:
    """Call Meko safely; never interrupt MatriSetu if Meko fails."""
    if not config.MEKO_ENABLED or not config.MEKO_API_KEY:
        return None

    try:
        return asyncio.run(
            asyncio.wait_for(
                _call_meko_async(tool_name, arguments),
                timeout=config.MEKO_TIMEOUT_SECONDS,
            )
        )
    except Exception as exc:
        logger.warning("Meko call failed (%s): %s", tool_name, type(exc).__name__)
        return None


def _result_text(result: Any) -> str:
    """Convert MCP tool output into readable text."""
    if result is None:
        return ""

    content = getattr(result, "content", None)
    if content:
        parts = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts)

    return str(result)


def create_conversation(title: str, datapack_id: str) -> Optional[str]:
    result = _safe_call(
        "conversation_create",
        {"title": title, "datapack_id": datapack_id},
    )
    return _extract_id(_result_text(result), "conversation_id")


def recall(
    conversation_id: str,
    message: str,
    patient_datapack: bool = True,
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
        "context_search",
        {
            "query": message,
            "conversation_id": conversation_id,
            "datapack_id": datapack_id,
            "limit": 5,
        },
    )

    return _result_text(result)


def remember(
    conversation_id: str,
    text: str,
    datapack_id: Optional[str] = None,
) -> Optional[str]:
    """Save an approved, de-identified memory."""
    if not conversation_id or not text:
        return None

    result = _safe_call(
        "memory_add",
        {
            "text": text,
            "conversation_id": conversation_id,
            "datapack_id": datapack_id or config.MEKO_PATIENTS_DATAPACK,
        },
    )

    return _extract_id(_result_text(result), "memory_id")