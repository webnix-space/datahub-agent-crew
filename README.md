# DataHub Agent Crew

Five-agent pipeline — Investigator, Analyst, Strategist, Regulatory, Codeband —
that reads a DataHub metadata graph via MCP, finds governance gaps
(unowned datasets, stale descriptions, missing compliance tags, broken
lineage), and writes corrected metadata back through `mcp-server-datahub`.

Built for "Build with DataHub: The Agent Hackathon" — Agents That Do Real
Work track.

## Status (2026-07-28)

- [x] DataHub self-hosted stack running (Codespace, `datahub docker quickstart`)
- [x] MCP mutation write path verified end-to-end (`update_description` →
      `EditableDatasetProperties`, confirmed via raw entity fetch)
- [x] Agent orchestration skeleton ported and fixed (this repo)
- [ ] Read-side MCP tool name confirmed (search/browse — run `list_tools()`
      against the live server, see TODO in `src/mcp_hooks.py`)
- [ ] SSE dashboard (deferred — proving the loop works beats a dashboard
      with nothing behind it, will revisit once agents run clean end-to-end)
- [ ] Demo video / submission writeup

## Architecture

```
Investigator --[gaps]--> Analyst --[triage]--> Strategist --[proposed fixes]-->
Regulatory --[cleared]--> Codeband --[writes to DataHub via MCP]--> loop
```

Inter-agent messaging is a local SQLite bus (`src/local_client.py`) — no
external message broker, no crypto payment rail. Each agent is a
long-running thread polling its own queue. Message routing is by
@mention; a message not addressed to an agent is dropped, not processed.

DataHub access is entirely inside `src/datahub_mcp.py` — nothing else
talks to DataHub directly. `src/mcp_hooks.py` wires specific agents
(Investigator for reads, Codeband for writes) to that client.

## Running it

```
pip install -r requirements.txt --break-system-packages
cp .env.example .env   # fill in AIML_API_KEY, GROQ_API_KEY
# make sure DataHub containers are up and `datahub docker quickstart`
# has been run at least once (see project doc for the recovery drill)
cd src
python orchestrator.py &
python seed_scan.py
```

Watch `orchestrator.py`'s log output for the five agents handing off.
