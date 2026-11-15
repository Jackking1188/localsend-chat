"""SQLite 本地存储：聊天记录、已知设备、事件总线。"""
from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id        TEXT PRIMARY KEY,
    peer_fp   TEXT NOT NULL DEFAULT '',
    peer_alias TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL,            -- in / out
    kind      TEXT NOT NULL DEFAULT 'text',  -- text / file / note
    body      TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL DEFAULT 0,
    ts        REAL NOT NULL,
    status    TEXT NOT NULL DEFAULT ''   -- 发送态：sending / sent / failed
);
CREATE INDEX IF NOT EXISTS idx_msg_peer ON messages(peer_fp, ts);

CREATE TABLE IF NOT EXISTS peers (
    fingerprint TEXT PRIMARY KEY,
    alias       TEXT NOT NULL DEFAULT '',
    device      TEXT NOT NULL DEFAULT '',
    ip          TEXT NOT NULL DEFAULT '',
    port        INTEGER NOT NULL DEFAULT 0,
    protocol    TEXT NOT NULL DEFAULT 'https',
    last_seen   REAL NOT NULL DEFAULT 0,
    trusted     INTEGER NOT NULL DEFAULT 1
);
"""


class Store:
    def __init__(self, path: Path | str = DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """给旧库补 status 列，避免升级后打不开原有聊天记录。"""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(messages)")}
        if "status" not in cols:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT ''")

    # ---------- 消息 ----------
    def add_message(
        self,
        direction: str,
        peer_fp: str,
        peer_alias: str,
        body: str,
        kind: str = "text",
        file_path: str = "",
        file_size: int = 0,
        ts: float | None = None,
        status: str = "",
    ) -> dict:
        mid = uuid.uuid4().hex
        ts = time.time() if ts is None else ts
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (id, peer_fp, peer_alias, direction, kind, body,"
                " file_path, file_size, ts, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mid, peer_fp, peer_alias, direction, kind, body, file_path, file_size, ts,
                 status),
            )
            self._conn.commit()
        return {
            "id": mid, "peer_fp": peer_fp, "peer_alias": peer_alias,
            "direction": direction, "kind": kind, "body": body,
            "file_path": file_path, "file_size": file_size, "ts": ts,
            "status": status,
        }

    def reset_stale_sending(self) -> int:
        """把残留的 sending 收尾成 failed。

        进程被杀 / 线程异常时，状态会永久停在 sending。启动时统一收尾，
        用户看到的就不再是永远转圈的「发送中」，而是可点击重发的「未送达」。
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE messages SET status='failed' WHERE status='sending'")
            self._conn.commit()
            return cur.rowcount

    def set_status(self, mid: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET status=? WHERE id=?", (status, mid))
            self._conn.commit()

    def history(self, peer_fp: str, limit: int = 300) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE peer_fp=? ORDER BY ts DESC LIMIT ?",
                (peer_fp, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def conversation_index(self) -> list[dict]:
        """每个会话最后一条消息，用于左侧列表预览。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.* FROM messages m JOIN ("
                "  SELECT peer_fp, MAX(ts) AS mt FROM messages GROUP BY peer_fp"
                ") t ON m.peer_fp=t.peer_fp AND m.ts=t.mt ORDER BY m.ts DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 设备 ----------
    def upsert_peer(self, info: dict, ip: str = "") -> None:
        fp = (info.get("fingerprint") or "").strip()
        if not fp:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO peers (fingerprint, alias, device, ip, port, protocol, last_seen)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(fingerprint) DO UPDATE SET"
                " alias=excluded.alias, device=excluded.device, ip=excluded.ip,"
                " port=excluded.port, protocol=excluded.protocol, last_seen=excluded.last_seen",
                (
                    fp,
                    info.get("alias") or "未知设备",
                    info.get("deviceModel") or "",
                    ip,
                    int(info.get("port") or 53317),
                    info.get("protocol") or "https",
                    time.time(),
                ),
            )
            self._conn.commit()

    def peers(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM peers ORDER BY last_seen DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 设备删除 / 屏蔽 ----------
    def clear_history(self, peer_fp: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE peer_fp=?", (peer_fp,))
            self._conn.commit()

    def delete_peer(self, fp: str, drop_history: bool = False) -> None:
        """删除会话（v2.5.1）：只移出列表，绝不写持久化名单。

        删除的本意是「不想看这段对话了」，不是「这台设备永远别出现」。
        设备还在线，下次刷新或组播回应就该重新出现。
        """
        fp = (fp or "").strip()
        if not fp:
            return
        with self._lock:
            self._conn.execute("DELETE FROM peers WHERE fingerprint=?", (fp,))
            if drop_history:
                self._conn.execute("DELETE FROM messages WHERE peer_fp=?", (fp,))
            self._conn.commit()

    def _ensure_hidden_table(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS hidden_peers ("
                " fingerprint TEXT PRIMARY KEY,"
                " alias TEXT NOT NULL DEFAULT '',"
                " hidden_at REAL NOT NULL DEFAULT 0)"
            )
            self._conn.commit()

    def hide_peer(self, fp: str, alias: str = "", drop_history: bool = False) -> None:
        """屏蔽设备（v2.5.1 起专用，删除动作不再调用这里）。

        屏蔽与删除是两种语义：只有用户明确选「屏蔽该设备」才写这份
        持久化名单，且必须能在「已屏蔽」列表里逐个或一键恢复。
        drop_history=True 时连带清空该设备的聊天记录。

        注：沿用旧表名 hidden_peers 以兼容已有数据库，语义已是 blocked。
        """
        fp = (fp or "").strip()
        if not fp:
            return
        self._ensure_hidden_table()
        with self._lock:
            self._conn.execute(
                "INSERT INTO hidden_peers (fingerprint, alias, hidden_at) VALUES (?,?,?)"
                " ON CONFLICT(fingerprint) DO UPDATE SET"
                " alias=excluded.alias, hidden_at=excluded.hidden_at",
                (fp, alias or "", time.time()),
            )
            self._conn.execute("DELETE FROM peers WHERE fingerprint=?", (fp,))
            if drop_history:
                self._conn.execute("DELETE FROM messages WHERE peer_fp=?", (fp,))
            self._conn.commit()

    def unhide_peer(self, fp: str) -> None:
        self._ensure_hidden_table()
        with self._lock:
            self._conn.execute("DELETE FROM hidden_peers WHERE fingerprint=?", (fp,))
            self._conn.commit()

    def clear_hidden(self) -> None:
        self._ensure_hidden_table()
        with self._lock:
            self._conn.execute("DELETE FROM hidden_peers")
            self._conn.commit()

    def hidden_fingerprints(self) -> set[str]:
        self._ensure_hidden_table()
        with self._lock:
            rows = self._conn.execute("SELECT fingerprint FROM hidden_peers").fetchall()
        return {r["fingerprint"] for r in rows}

    def hidden_peers(self) -> list[dict]:
        self._ensure_hidden_table()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM hidden_peers ORDER BY hidden_at DESC").fetchall()
        return [dict(r) for r in rows]

    # --- v2.5.1 屏蔽语义别名（UI 只读这一组） ---
    def block_peer(self, fp: str, alias: str = "") -> None:
        self.hide_peer(fp, alias, drop_history=False)

    def unblock_peer(self, fp: str) -> None:
        self.unhide_peer(fp)

    def clear_blocked(self) -> None:
        self.clear_hidden()

    def blocked_fingerprints(self) -> set[str]:
        return self.hidden_fingerprints()

    def blocked_peers(self) -> list[dict]:
        return self.hidden_peers()

    def trusted_fingerprints(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT fingerprint FROM peers WHERE trusted=1"
            ).fetchall()
        return {r["fingerprint"] for r in rows}

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception:
            pass


class Bus:
    """线程安全事件队列：后台线程 -> UI 主线程。"""

    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[str, dict]]" = queue.Queue()

    def emit(self, kind: str, **payload) -> None:
        self._q.put((kind, payload))

    def drain(self, limit: int = 64) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        for _ in range(limit):
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out


def dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False)
