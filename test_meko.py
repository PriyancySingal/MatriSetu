import asyncio
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

import config


def _contains_exception(error: BaseException, exception_type: type[BaseException]) -> bool:
    if isinstance(error, exception_type):
        return True

    return any(
        _contains_exception(nested, exception_type)
        for nested in getattr(error, "exceptions", ())
    )


def _exception_type_names(error: BaseException) -> list[str]:
    nested_errors = getattr(error, "exceptions", ())
    if not nested_errors:
        return [type(error).__name__]

    return [
        name
        for nested in nested_errors
        for name in _exception_type_names(nested)
    ]


async def main():
    if not config.MEKO_ENABLED:
        print("Meko is disabled.")
        return

    if not config.MEKO_API_KEY:
        print("Meko API key is missing.")
        return

    headers = {
        "Authorization": f"Bearer {config.MEKO_API_KEY}",
        "User-Agent": "matrisetu/1.0",
    }
    authentication_failure_status = None

    async def record_authentication_failure(response: httpx2.Response) -> None:
        nonlocal authentication_failure_status
        if response.status_code in (401, 403):
            authentication_failure_status = response.status_code

    try:
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=httpx2.Timeout(config.MEKO_TIMEOUT_SECONDS),
            event_hooks={"response": [record_authentication_failure]},
        ) as http_client:
            async with streamable_http_client(
                config.MEKO_MCP_URL,
                http_client=http_client,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    print("Meko connection successful!")

                    result = await session.list_tools()

                    print(f"Available tools: {len(result.tools)}")

                    for tool in result.tools:
                        print(f"- {tool.name}")

    except Exception as error:
        if authentication_failure_status is not None:
            print(
                "Meko authentication failed "
                f"(HTTP {authentication_failure_status}). Check the configured API key."
            )
        elif _contains_exception(error, httpx2.TimeoutException):
            print(
                "Meko connection timed out after "
                f"{config.MEKO_TIMEOUT_SECONDS} seconds."
            )
        elif _contains_exception(error, httpx2.RequestError):
            print(f"Meko connection failed: {type(error).__name__}. Check the endpoint and network.")
        elif isinstance(error, MCPError):
            print(f"Meko MCP request failed with protocol error code {error.code}.")
        else:
            exception_types = ", ".join(_exception_type_names(error))
            print(f"Meko connection failed ({exception_types}).")


if __name__ == "__main__":
    asyncio.run(main())