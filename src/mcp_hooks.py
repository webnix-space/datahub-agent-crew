"""
Where agents actually touch DataHub. Kept separate from base_agent.py on
purpose — polling/threading plumbing shouldn't know anything about MCP,
and MCP logic shouldn't know anything about the message bus.

Each hook opens its own short-lived DataHubMCP session (asyncio.run per
call). That means spawning `uvx mcp-server-datahub` fresh on every
message. Fine for a hackathon demo cadence (messages every few seconds,
not per-second). If this becomes the bottleneck, the fix is a persistent
session per thread with a lock — not now, don't build it before you need it.
"""
import logging
import re

from datahub_mcp import DataHubMCP, run_async

logger = logging.getLogger(__name__)

URN_DESC_PATTERN = re.compile(
    r"URN:\s*(?P<urn>\S+)\s*\n\s*DESCRIPTION:\s*(?P<desc>.+?)(?=\nURN:|\Z)",
    re.DOTALL,
)


def investigator_hook(agent_name: str, content: str) -> str:
    """
    Investigator's job: find datasets with gaps (no description, no owner,
    missing compliance tags, broken lineage) and hand a concrete list to
    the Analyst.

    Confirmed working 2026-07-29 (verified live via test_write.py): tool
    is 'search', query format {"query": "type:dataset"}. No more guessing.
    """
    async def _run():
        async with DataHubMCP() as dh:
            try:
                result = await dh.call_tool("search", {"query": "type:dataset"})
            except Exception as e:
                return f"[MCP CALL FAILED] search: {e}"
            return f"Live query via 'search':\n{result}"

    return run_async(_run())


def codeband_hook(agent_name: str, content: str) -> str:
    """
    Codeband's job: take Regulatory-cleared remediation and actually write
    it back. Expects the upstream message (Regulatory's reply, which is
    what triggers Codeband via @mention) to contain one or more blocks:

        URN: urn:li:dataset:(...)
        DESCRIPTION: <the corrected description text>

    If Regulatory/Strategist aren't emitting that exact shape yet, this
    returns a hint instead of silently doing nothing — check the prompts
    in orchestrator.py first before assuming this hook is broken.
    """
    matches = list(URN_DESC_PATTERN.finditer(content))
    if not matches:
        return (
            "[NO WRITE-BACK TARGETS FOUND] Expected 'URN: ...' / 'DESCRIPTION: ...' "
            "pairs in the incoming message and found none. If Strategist/Regulatory "
            "aren't producing that format yet, fix the prompt, not this hook."
        )

    async def _run():
        results = []
        async with DataHubMCP() as dh:
            for m in matches:
                urn = m.group("urn").strip()
                desc = m.group("desc").strip()
                try:
                    await dh.update_description(entity_urn=urn, description=desc, operation="replace")
                    results.append(f"WRITTEN: {urn}")
                except Exception as e:
                    results.append(f"FAILED: {urn} — {e}")
        return "\n".join(results)

    return run_async(_run())
