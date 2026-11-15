"""本地 HTTPS 服务端：实现 LocalSend Protocol v2 的接收侧。

与原版 LocalSend 的差异仅在于「收到内容之后做什么」——原版写入下载目录并在 UI 里
展示传输记录，这里把 text/plain 解析成聊天消息写进 SQLite，把文件落盘同样记录为一条
消息，从而在不改动协议的前提下获得聊天窗口语义。
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import certs
from .config import CERT_DIR, Config
from .storage import Store

API_V2 = "/api/localsend/v2"
API_V1 = "/api/localsend/v1"


class Session:
    __slots__ = ("sid", "files", "alias", "fp", "left", "created", "last")

    def __init__(self, sid: str, files: dict, alias: str, fp: str) -> None:
        self.sid = sid
        self.files = files
        self.alias = alias
        self.fp = fp
        self.left = set(files.keys())
        self.created = self.last = time.time()


class Server:
    def __init__(self, cfg: Config, store: Store, bus, my_info_provider) -> None:
        self.cfg = cfg
        self.store = store
        self.bus = bus
        self.my_info = my_info_provider
        self.sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self.httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = cfg.port

    # ---------- 生命周期 ----------
    def start(self) -> None:
        handler = _make_handler(self)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(CERT_DIR / "cert.pem"), str(CERT_DIR / "key.pem"))

        last_err: Exception | None = None
        for port in self._port_candidates():
            try:
                httpd = ThreadingHTTPServer(("", port), handler)
                httpd.daemon_threads = True
                httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
                self.httpd = httpd
                self.port = port
                break
            except OSError as e:
                last_err = e
                continue

        if self.httpd is None:
            raise RuntimeError(
                f"无法在端口 {self._port_candidates()} 上启动服务：{last_err}"
            )

        self._thread = threading.Thread(
            target=self.httpd.serve_forever, name="ls-http", daemon=True
        )
        self._thread.start()
        self._reap()

    def _port_candidates(self):
        yield self.cfg.port
        for p in range(self.cfg.port + 1, self.cfg.port + 12):
            yield p
        yield 0

    def stop(self) -> None:
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None

    def _reap(self) -> None:
        """清理超过 10 分钟未完成的会话。"""

        def run():
            while self.httpd is not None:
                cutoff = time.time() - 600
                with self._lock:
                    for sid in [s for s, v in self.sessions.items() if v.last < cutoff]:
                        self.sessions.pop(sid, None)
                time.sleep(60)

        threading.Thread(target=run, name="ls-reap", daemon=True).start()

    # ---------- 供 handler 调用 ----------
    def peer_view(self, info: dict, ip: str) -> dict:
        return {
            "alias": info.get("alias") or "未知设备",
            "fingerprint": info.get("fingerprint") or "",
            "deviceModel": info.get("deviceModel") or "",
            "deviceType": info.get("deviceType") or "",
            "ip": ip,
            "port": int(info.get("port") or 53317),
            "protocol": info.get("protocol") or "https",
        }

    def accept(self, files: dict, info: dict, ip: str) -> tuple[str, dict]:
        sid = uuid.uuid4().hex
        view = self.peer_view(info, ip)
        with self._lock:
            self.sessions[sid] = Session(
                sid, files, view["alias"], view["fingerprint"]
            )
        tokens = {fid: uuid.uuid4().hex for fid in files}
        return sid, tokens


def _make_handler(server: Server):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "LocalSendChat/1.0"

        # --- 基础 ---
        def log_message(self, fmt, *args):
            return

        def _send_json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

        def _send_empty(self, code: int) -> None:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _body(self) -> bytes:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except Exception:
                n = 0
            if n <= 0:
                return b""
            chunks: list[bytes] = []
            remaining = n
            while remaining > 0:
                block = self.rfile.read(min(remaining, 1 << 20))
                if not block:
                    break
                chunks.append(block)
                remaining -= len(block)
            return b"".join(chunks)

        def _json_body(self) -> dict:
            raw = self._body()
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        # --- 路由 ---
        def do_GET(self):
            path = urlparse(self.path).path.rstrip("/")
            if path in (f"{API_V2}/info", f"{API_V1}/info", "/info"):
                return self._send_json(200, server.my_info())
            if path == "/":
                return self._send_json(200, {"app": "LocalSend Chat", "ok": True})
            return self._send_json(404, {"error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            query = parse_qs(parsed.query)

            if path in (f"{API_V2}/register", f"{API_V1}/register"):
                return self._handle_register()

            if path in (f"{API_V2}/prepare-upload", f"{API_V1}/send-request"):
                return self._handle_prepare()

            if path in (f"{API_V2}/upload", f"{API_V1}/send"):
                return self._handle_upload(query)

            if path in (f"{API_V2}/cancel", f"{API_V1}/cancel"):
                sid = (query.get("sessionId") or [""])[0]
                with server._lock:
                    server.sessions.pop(sid, None)
                return self._send_empty(200)

            return self._send_json(404, {"error": "not found"})

        # --- 具体处理 ---
        def _handle_register(self):
            data = self._json_body()
            info = data.get("info") if isinstance(data.get("info"), dict) else data
            ip = self.client_address[0]
            view = server.peer_view(info or {}, ip)
            if view["fingerprint"]:
                server.store.upsert_peer(view, ip)
                server.bus.emit("peer", peer=view)
            return self._send_json(200, {"info": server.my_info(), "peers": []})

        def _handle_prepare(self):
            data = self._json_body()
            files = data.get("files")
            if not isinstance(files, dict) or not files:
                return self._send_json(400, {"error": "no files"})
            info = data.get("info") if isinstance(data.get("info"), dict) else {}
            ip = self.client_address[0]
            view = server.peer_view(info, ip)

            if view["fingerprint"]:
                server.store.upsert_peer(view, ip)
                server.bus.emit("peer", peer=view)

            sid, tokens = server.accept(files, info, ip)

            # 记录这一批将要进来的东西，用于 UI 提示
            summary = []
            for fid, meta in files.items():
                summary.append({
                    "fileName": meta.get("fileName") or f"file-{fid}",
                    "size": int(meta.get("size") or 0),
                    "fileType": meta.get("fileType") or "",
                })
            server.bus.emit(
                "incoming", alias=view["alias"], fingerprint=view["fingerprint"],
                sessionId=sid, files=summary,
            )
            return self._send_json(200, {"sessionId": sid, "files": tokens})

        def _handle_upload(self, query: dict):
            sid = (query.get("sessionId") or [""])[0]
            fid = (query.get("fileId") or [""])[0]
            with server._lock:
                sess = server.sessions.get(sid)
            if sess is None:
                return self._send_json(403, {"error": "invalid session"})
            sess.last = time.time()

            meta = sess.files.get(fid) or {}
            payload = self._body()

            ftype = (meta.get("fileType") or "").lower()
            name = meta.get("fileName") or f"file-{fid}"
            is_text = ftype.startswith("text/plain") or ftype == "text"

            if is_text:
                text = payload.decode("utf-8", "replace")
                msg = server.store.add_message(
                    "in", sess.fp, sess.alias, text, kind="text"
                )
                server.bus.emit("message", message=msg, alias=sess.alias,
                                fingerprint=sess.fp)
            else:
                try:
                    out_dir = server.cfg.download_dir
                    out_path = _unique_path(out_dir, os.path.basename(name))
                    out_path.write_bytes(payload)
                    msg = server.store.add_message(
                        "in", sess.fp, sess.alias, name, kind="file",
                        file_path=str(out_path), file_size=len(payload),
                    )
                    server.bus.emit("message", message=msg, alias=sess.alias,
                                    fingerprint=sess.fp)
                    server.bus.emit("file", path=str(out_path), alias=sess.alias)
                except Exception as e:
                    server.bus.emit("error", text=f"保存文件失败：{e}")
                    return self._send_json(500, {"error": "save failed"})

            sess.left.discard(fid)
            if not sess.left:
                with server._lock:
                    server.sessions.pop(sid, None)
            return self._send_empty(200)

    return Handler


def _unique_path(base: Path, name: str) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    candidate = base / name
    if not candidate.exists():
        return candidate
    stem, suffix = os.path.splitext(name)
    for i in range(1, 10000):
        alt = base / f"{stem} ({i}){suffix}"
        if not alt.exists():
            return alt
    return base / f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"
