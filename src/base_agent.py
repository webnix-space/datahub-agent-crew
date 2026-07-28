"""
Base polling agent for DataHub Agent Crew.

Ported from ShadowSignal's base_agent.py with these changes:
  - Removed BrightDataClient / free_data.py entirely. Real-time context now
    comes from DataHub via datahub_mcp.py, not RSS/DuckDuckGo scraping.
  - Removed the dead nanopayment/payment-log code path (it wasn't doing
    anything in the source anyway — the docstring lied, the code didn't).
  - Fixed the LLM call routing bug: original call_llm() let a global
    GROQ_API_KEY silently override whatever provider/model was passed in
    per-agent. Each agent now strictly uses its own configured
    key/model/base_url. No silent override.
  - Everything else (LocalClient bus, dedup via sqlite, loop-trigger
    detection, mention-based handoff) is the same shape because it works
    and there's no reason to rebuild it.
"""
import logging
import os
import re
import sqlite3
import time
from datetime import datetime

import requests

from local_client import LocalClient

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
ROOM_ID = os.getenv("ROOM_ID", "datahub-crew-room")

AGENT_ORDER = [
    "DataHub Investigator",
    "DataHub Analyst",
    "DataHub Strategist",
    "DataHub Regulatory",
    "DataHub Codeband",
]

LOOP_TRIGGERS = [
    "[CODEBAND] workflow complete",
    "[CODEBAND] BLOCKED",
    "@AnalystAgent gaps ready",
    "@StrategistAgent triage complete",
    "@RegulatoryAgent cleared",
    "@RegulatoryAgent BLOCKED",
]


def call_llm(messages: list, api_key: str, model: str, base_url: str, max_retries: int = 3) -> str:
    if not api_key or not base_url or not model:
        raise ValueError(f"Incomplete LLM config: base_url={base_url!r} model={model!r} api_key_set={bool(api_key)}")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": 2048, "temperature": 0.5}
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=45)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.exceptions.HTTPError as e:
            last_err = e
            if resp.status_code == 429:
                time.sleep(2 ** attempt + 1)
            else:
                raise
        except Exception as e:
            last_err = e
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM call failed after {max_retries} attempts: {last_err}")


def safe_get(obj, *keys, default=None):
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
    return obj if obj is not None else default


def extract_mentions(content, participants, self_id="", self_name=""):
    if not content or not participants:
        return []
    mentions = []
    content_lower = content.lower()
    for p in participants:
        if not isinstance(p, dict):
            continue
        agent_id = p.get("id") or safe_get(p, "agent", "id")
        name = p.get("name", "")
        handle = p.get("handle", "")
        if not agent_id:
            continue
        if self_id and agent_id == self_id:
            continue
        if self_name and name and name.lower() == self_name.lower():
            continue
        if name and not name.startswith("DataHub"):
            continue
        checks = []
        if name:
            checks.append(f"@{name}".lower() in content_lower)
            checks.append(f"@{name.lower().replace(' ', '')}" in content_lower)
        if handle:
            checks.append(f"@{handle}".lower() in content_lower)
        if any(checks):
            mentions.append({"id": agent_id, "name": name, "handle": handle or name.lower().replace(" ", "")})
    return mentions


class BasePollingAgent:
    def __init__(self, name, system_prompt, llm_api_key, llm_model, llm_base_url,
                 room_id=None, mcp_hook=None):
        """
        mcp_hook: optional callable(agent_name: str, content: str) -> str
                  Runs before the LLM call. Returns extra context text (or "")
                  to append to the user message — this is where DataHub reads
                  (search/get gaps) or writes (update_description) get
                  triggered, kept out of the polling/threading plumbing.
        """
        self.name = name
        self.client = LocalClient(name)
        self.system_prompt = system_prompt
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.llm_base_url = llm_base_url
        self.room_id = room_id or ROOM_ID
        self.history = [{"role": "system", "content": system_prompt}]
        self.my_id = None
        self.my_name = name
        self.participants_cache = []
        self.mcp_hook = mcp_hook

        data_dir = os.getenv("DATA_DIR", "/tmp")
        os.makedirs(data_dir, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower())
        self.db_path = f"{data_dir}/dhac_{safe_name}_processed.db"
        self._init_db()
        self._processed_cache = set()
        self._load_cache()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.cursor().execute(
            "CREATE TABLE IF NOT EXISTS processed_messages "
            "(msg_id TEXT PRIMARY KEY, processed_at TEXT, agent_name TEXT)"
        )
        conn.commit()
        conn.close()

    def _load_cache(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "SELECT msg_id FROM processed_messages WHERE agent_name = ? ORDER BY processed_at DESC LIMIT 2000",
            (self.name,),
        )
        self._processed_cache = {row[0] for row in c.fetchall()}
        conn.close()

    def _is_processed(self, msg_id):
        if msg_id in self._processed_cache:
            return True
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT 1 FROM processed_messages WHERE msg_id = ? AND agent_name = ?", (msg_id, self.name))
        result = c.fetchone() is not None
        conn.close()
        return result

    def _mark_processed(self, msg_id):
        if msg_id in self._processed_cache:
            return
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        try:
            c.execute(
                "INSERT OR IGNORE INTO processed_messages (msg_id, processed_at, agent_name) VALUES (?, ?, ?)",
                (msg_id, datetime.utcnow().isoformat(), self.name),
            )
            conn.commit()
            self._processed_cache.add(msg_id)
        finally:
            conn.close()

    def _is_loop_message(self, content, sender_name):
        content_lower = content.lower()
        for trigger in LOOP_TRIGGERS:
            if trigger.lower() in content_lower:
                try:
                    if AGENT_ORDER.index(sender_name) > AGENT_ORDER.index(self.name):
                        return True
                except ValueError:
                    pass
        return False

    def _addressed_to_me(self, content: str) -> bool:
        content_lower = content.lower()
        name_hit = f"@{self.my_name}".lower() in content_lower or f"@{self.name}".lower() in content_lower
        handle_hit = f"@{self.name.lower().replace(' ', '')}" in content_lower
        if name_hit or handle_hit:
            return True
        # No @mention anywhere in the message at all (e.g. a human's
        # kickoff prompt) — only the first agent in the chain should
        # treat that as its cue to start. Everyone else stays silent.
        has_any_mention = "@" in content
        if not has_any_mention:
            return self.name == AGENT_ORDER[0]
        return False

    def _get_next_agent_full_name(self):
        try:
            idx = AGENT_ORDER.index(self.name)
            if idx + 1 < len(AGENT_ORDER):
                return AGENT_ORDER[idx + 1]
        except ValueError:
            pass
        return ""

    def run(self):
        try:
            me = self.client.me()
            self.my_id = me.get("id")
            self.my_name = me.get("name", self.name)
            logger.info(f"[{self.name}] Connected. ID={self.my_id}")
        except Exception as e:
            logger.error(f"[{self.name}] Connection failed: {e}")
            return

        try:
            self.participants_cache = self.client.get_participants(self.room_id)
        except Exception as e:
            logger.warning(f"[{self.name}] Participants fetch failed: {e}")

        logger.info(f"[{self.name}] running, model={self.llm_model}")

        while True:
            try:
                msg = self.client.get_next_message(self.room_id)
                if msg is not None:
                    self._handle_message(msg)
                else:
                    time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"[{self.name}] Poll error: {e}", exc_info=True)
                time.sleep(POLL_INTERVAL)

    def _handle_message(self, msg):
        if not isinstance(msg, dict):
            return
        message_id = msg.get("id", "")
        if message_id and self._is_processed(message_id):
            return
        content = str(msg.get("content", ""))
        sender_obj = msg.get("sender") or {}
        sender_id = sender_obj.get("id", "")
        sender_name = sender_obj.get("name", "unknown")

        is_own = (self.my_id and sender_id == self.my_id) or (sender_name.lower() == self.my_name.lower())
        if is_own or self._is_loop_message(content, sender_name):
            if message_id:
                self._mark_processed(message_id)
                self.client.mark_processed(self.room_id, message_id)
            return

        if not self._addressed_to_me(content):
            # LocalClient broadcasts to every agent except the sender —
            # it does NOT filter by mentions. Without this check, every
            # agent runs the LLM on every message in the room and relies
            # on the system prompt alone to stay quiet. That's not
            # routing, that's hoping. Drop it silently instead.
            if message_id:
                self._mark_processed(message_id)
                self.client.mark_processed(self.room_id, message_id)
            return

        if message_id:
            self._mark_processed(message_id)
            self.client.mark_processing(self.room_id, message_id)

        try:
            user_message = f"[{sender_name}]: {content}"

            if self.mcp_hook:
                try:
                    extra = self.mcp_hook(self.name, content)
                    if extra:
                        user_message += f"\n\n--- DATAHUB CONTEXT ---\n{extra}\n--- END ---"
                except Exception as e:
                    logger.error(f"[{self.name}] mcp_hook failed: {e}", exc_info=True)
                    user_message += f"\n\n[DATAHUB CONTEXT UNAVAILABLE: {e}]"

            self.history.append({"role": "user", "content": user_message})
            reply = call_llm(self.history, self.llm_api_key, self.llm_model, self.llm_base_url)
            self.history.append({"role": "assistant", "content": reply})

            mentions = extract_mentions(reply, self.participants_cache, self.my_id, self.my_name)
            if not mentions:
                next_name = self._get_next_agent_full_name()
                if next_name:
                    for p in self.participants_cache:
                        if p.get("name", "").lower() == next_name.lower():
                            mentions.append(p)
                            break

            if mentions:
                self.client.send_message(self.room_id, reply, mentions)
            else:
                self.client.post_event(self.room_id, reply[:1000], message_type="thought")

            if message_id:
                self.client.mark_processed(self.room_id, message_id)

        except Exception as e:
            logger.error(f"[{self.name}] Error: {e}", exc_info=True)
            if message_id:
                self.client.mark_failed(self.room_id, message_id, str(e))
