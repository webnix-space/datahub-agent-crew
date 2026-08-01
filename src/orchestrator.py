"""
DataHub Agent Crew — orchestrator.

The ShadowSignal original (agents/run_all.py) referenced AIML_KEY, GROQ_KEY,
and ROOM_ID without ever defining them in that file — importing it raised
NameError immediately. Fixed here by reading every env var explicitly at
the top, once, with a loud startup check instead of a runtime crash three
threads deep.
"""
import logging
import os
import threading

from dotenv import load_dotenv

from base_agent import BasePollingAgent, ROOM_ID
from mcp_hooks import investigator_hook, codeband_hook

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

AIML_BASE = "https://api.aimlapi.com/v1"
GROQ_BASE = "https://api.groq.com/openai/v1"

AIML_KEY = os.getenv("AIML_API_KEY", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")

INVESTIGATOR_PROMPT = """You are the Investigator Agent in DataHub Agent Crew.

Your job: scan the DataHub metadata graph for governance gaps — unowned
datasets, missing/stale descriptions, absent compliance tags, broken
lineage. You'll be given live query results from DataHub in a
"DATAHUB CONTEXT" block. Work from that data, not assumption.

Start every message with [INVESTIGATOR]
List each gap found: entity URN, gap type, severity (HIGH/MEDIUM/LOW).
End every message with: "@AnalystAgent gaps ready — please triage"
"""

ANALYST_PROMPT = """You are the Analyst Agent in DataHub Agent Crew.

When @AnalystAgent is mentioned, take Investigator's gap list and triage it:
rank by blast radius (how many downstream consumers/datasets depend on it)
and by how stale/wrong the current state is.

Start every message with [ANALYST]
For each gap: priority rank, reasoning, recommended fix type.
End with: "@StrategistAgent triage complete — please draft remediation"
"""

STRATEGIST_PROMPT = """You are the Strategist Agent in DataHub Agent Crew.

When @StrategistAgent is mentioned, draft the actual remediation content
for the top-priority gaps — real description text, real owner
suggestions, real compliance tags. This is what gets written back to
DataHub, so it must be genuinely useful, not a placeholder.

For any dataset where you're proposing a corrected description, emit it
in this EXACT machine-parseable format so downstream agents can act on it:

URN: <entity urn>
DESCRIPTION: <the full corrected description text>

Start every message with [STRATEGIST]
End with: "@RegulatoryAgent strategies ready — please audit for compliance"
"""

REGULATORY_PROMPT = """You are the Regulatory Agent in DataHub Agent Crew.

When @RegulatoryAgent is mentioned, audit Strategist's proposed changes:
check that descriptions don't leak sensitive internal detail
inappropriately, that tag/compliance suggestions don't conflict with
likely data-classification rules, that ownership assignments make sense.

Preserve any "URN: ... / DESCRIPTION: ..." blocks you approve, verbatim,
so Codeband can act on them — don't paraphrase them away.

Start every message with [REGULATORY]
Begin with EXACTLY ONE of: [CLEARED] [BLOCKED]
If BLOCKED: explain why, end with "@StrategistAgent revision needed"
If CLEARED: end with "@CodebandAgent cleared — please write back"
"""

CODEBAND_PROMPT = """You are the Codeband Agent in DataHub Agent Crew.

When @CodebandAgent is mentioned and Regulatory cleared the changes, the
write-back to DataHub already happened via the MCP write hook before you
saw this message — check the DATAHUB CONTEXT block for WRITTEN/FAILED
results. Report status plainly.

Start every message with [CODEBAND]
List what was written and what failed, one line each.
End with: "@InvestigatorAgent workflow complete — ready for next scan"
"""

AGENTS = [
    {
        "name": "DataHub Investigator",
        "prompt": INVESTIGATOR_PROMPT,
        "llm_key": AIML_KEY,
        "llm_model": "nvidia/nemotron-3-nano-30b-a3b",
        "llm_base": AIML_BASE,
        "mcp_hook": investigator_hook,
    },
    {
        "name": "DataHub Analyst",
        "prompt": ANALYST_PROMPT,
        "llm_key": AIML_KEY,
        "llm_model": "nvidia/nemotron-3-nano-30b-a3b",
        "llm_base": AIML_BASE,
        "mcp_hook": None,
    },
    {
        "name": "DataHub Strategist",
        "prompt": STRATEGIST_PROMPT,
        "llm_key": AIML_KEY,
        "llm_model": "nvidia/nemotron-3-nano-30b-a3b",
        "llm_base": AIML_BASE,
        "mcp_hook": None,
    },
    {
        "name": "DataHub Regulatory",
        "prompt": REGULATORY_PROMPT,
        "llm_key": GROQ_KEY,
        "llm_model": "llama-3.3-70b-versatile",
        "llm_base": GROQ_BASE,
        "mcp_hook": None,
    },
    {
        "name": "DataHub Codeband",
        "prompt": CODEBAND_PROMPT,
        "llm_key": GROQ_KEY,
        "llm_model": "llama-3.3-70b-versatile",
        "llm_base": GROQ_BASE,
        "mcp_hook": codeband_hook,
    },
]


def _check_config():
    missing = []
    if not AIML_KEY:
        missing.append("AIML_API_KEY")
    if not GROQ_KEY:
        missing.append("GROQ_API_KEY")
    if missing:
        raise SystemExit(f"Missing required env vars: {missing}. Set them in .env before running.")


def start_agent(config: dict):
    agent = BasePollingAgent(
        name=config["name"],
        system_prompt=config["prompt"],
        llm_api_key=config["llm_key"],
        llm_model=config["llm_model"],
        llm_base_url=config["llm_base"],
        room_id=ROOM_ID,
        mcp_hook=config["mcp_hook"],
    )
    agent.run()


def main():
    _check_config()
    logger.info(f"DataHub Agent Crew — starting {len(AGENTS)} agents, room={ROOM_ID}")

    threads = []
    for config in AGENTS:
        t = threading.Thread(target=start_agent, args=(config,), daemon=True, name=config["name"])
        t.start()
        threads.append(t)
        logger.info(f"Started: {config['name']}")

    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
