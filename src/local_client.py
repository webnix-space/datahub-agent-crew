"""
Local message bus — drop-in replacement for BandClient.
Same method signatures. Zero external dependencies. SQLite-backed.
"""
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

FIXED_AGENTS = [
    "ShadowSignal Investigator",
    "ShadowSignal Analyst",
    "ShadowSignal Strategist",
    "ShadowSignal Regulatory",
    "ShadowSignal Codeband",
]


class LocalClient:
    def __init__(self, agent_name: str):
        self.name = agent_name
        self.my_id = agent_name.lower().replace(" ", "_")
        data_dir = os.getenv("DATA_DIR", "/tmp")
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = f"{data_dir}/shadowsignal_bus.db"
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                row_id TEXT PRIMARY KEY,
                msg_id TEXT,
                chat_id TEXT,
                recipient_name TEXT,
                sender_id TEXT,
                sender_name TEXT,
                content TEXT,
                mentions TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def me(self) -> dict:
        return {"id": self.my_id, "name": self.name}

    def get_chats(self) -> list:
        return [{"id": "local-room", "name": "ShadowSignal Local Room"}]

    def get_participants(self, chat_id: str) -> list:
        return [
            {"id": a.lower().replace(" ", "_"), "name": a, "handle": a.lower().replace(" ", "")}
            for a in FIXED_AGENTS
        ]

    def send_message(self, chat_id: str, content: str, mentions: list = None) -> dict:
        msg_id = str(uuid.uuid4())
        conn = sqlite3.connect(self.db_path)
        recipients = [a for a in FIXED_AGENTS if a != self.name]
        for recipient in recipients:
            conn.execute(
                "INSERT INTO queue (row_id, msg_id, chat_id, recipient_name, sender_id, sender_name, content, mentions, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), msg_id, chat_id, recipient, self.my_id, self.name,
                 content, json.dumps(mentions or []), "pending", datetime.utcnow().isoformat())
            )
        conn.commit()
        conn.close()
        logger.info(f"[LocalClient] {self.name} sent message to {len(recipients)} agents")
        return {"id": msg_id, "content": content}

    def post_event(self, chat_id: str, content: str, message_type: str = "thought") -> dict:
        logger.info(f"[LocalClient] [{self.name}] EVENT ({message_type}): {content[:200]}")
        return {"status": "logged"}

    def get_next_message(self, chat_id: str) -> dict | None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "SELECT row_id, msg_id, sender_id, sender_name, content, mentions FROM queue "
            "WHERE recipient_name = ? AND status = 'pending' ORDER BY created_at ASC LIMIT 1",
            (self.name,)
        )
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        row_id, msg_id, sender_id, sender_name, content, mentions = row
        return {
            "id": row_id,
            "content": content,
            "sender": {"id": sender_id, "name": sender_name},
            "mentions": json.loads(mentions or "[]"),
        }

    def get_messages(self, chat_id: str, limit: int = 50) -> list:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "SELECT row_id, sender_id, sender_name, content FROM queue "
            "WHERE recipient_name = ? ORDER BY created_at DESC LIMIT ?",
            (self.name, limit)
        )
        rows = c.fetchall()
        conn.close()
        return [{"id": r[0], "sender": {"id": r[1], "name": r[2]}, "content": r[3]} for r in rows]

    def mark_processing(self, chat_id: str, message_id: str) -> None:
        self._set_status(message_id, "processing")

    def mark_processed(self, chat_id: str, message_id: str) -> None:
        self._set_status(message_id, "processed")

    def mark_failed(self, chat_id: str, message_id: str, error: str = "") -> None:
        self._set_status(message_id, "failed")

    def _set_status(self, row_id: str, status: str) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE queue SET status = ? WHERE row_id = ?", (status, row_id))
        conn.commit()
        conn.close()

    def get_context(self, chat_id: str) -> dict:
        return {"messages": self.get_messages(chat_id, limit=100)}

    def get_peers(self, not_in_chat: str = None) -> list:
        return self.get_participants(not_in_chat or "local-room")
