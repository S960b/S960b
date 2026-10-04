"""Minimal tested compatibility hook for mcp==2.3.0.

PROOF ONLY: fixes capability advertisement, not the rest of the bridge.
No bypass of HTTP authentication, parameter validation, or SDK handlers.
Install inside make_mcp_server before returning its server:
    install_discover_events_compat(mcp)
Pin mcp and mcp-types versions; test wire output after dependency updates.
"""


def install_discover_events_compat(mcp):
    async def advertise_after_sdk_serialization(ctx, call_next):
        result = await call_next(ctx)
        if (ctx.method == "server/discover"
                and ctx.protocol_version == "2026-07-28"
                and isinstance(result, dict)
                and result.get("resultType") == "complete"):
            result = dict(result)
            result["capabilities"] = dict(result.get("capabilities", {}))
            result["capabilities"]["events"] = {}
        return result

    mcp._lowlevel_server.middleware.append(advertise_after_sdk_serialization)
