"""LocalSend Protocol v2 客户端：发送文字 / 文件，注册自身。

只做协议实现，不改协议语义：
  POST /api/localsend/v2/register          {"info": {...}} -> {"info": {...}, "peers": [...]}
  POST /api/localsend/v2/prepare-upload    {"info": {...}, "files": {id: meta}} -> {sessionId, files:{id:token}}
  POST /api/localsend/v2/upload?sessionId&fileId&token   <binary>
  POST /api/localsend/v2/cancel?sessionId

两条来自实测的修正：

1. **每条传输使用唯一的 fileId**。早先固定用 "1"，对方若残留同名会话或按 fileId
   去重，会直接回 204/403，表现就是「对方拒接了本次传输」，而实际上用户已经点了接受。

2. **完整记录协议轨迹到 protocol.log**。204/403/401 的含义完全不同，必须能区分
   到底是「用户点了拒绝」「文件被拒」还是「需要 PIN」，否则只能猜。

文字消息在协议层等价于一个 fileType=text/plain 的小文件，正文即文件内容，
这与官方客户端「发送消息」的行为完全一致，因此对端即使是原版 LocalSend 也能正常收到。
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

API = "/api/localsend/v2"

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE   # 自签名互通；指纹在 storage 层做 TOFU
_OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_CTX))

_LOG_LOCK = None


def _log_path() -> Path:
    try:
        from .config import DATA_DIR
        return DATA_DIR / "protocol.log"
    except Exception:
        return Path("protocol.log")


def trace(direction: str, text: str) -> None:
    """追加一行协议轨迹。写失败不影响主流程。"""
    global _LOG_LOCK
    try:
        if _LOG_LOCK is None:
            import threading
            _LOG_LOCK = threading.Lock()
        line = f"{time.strftime('%m-%d %H:%M:%S')} [{direction}] {text}\n"
        with _LOG_LOCK:
            p = _log_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
            # 超 512KB 截半，避免无限增长
            if p.stat().st_size > 512 * 1024:
                kept = p.read_text(encoding="utf-8", errors="replace")[-262144:]
                p.write_text(kept, encoding="utf-8")
    except Exception:
        pass


@dataclass
class SendResult:
    ok: bool
    message: str = ""
    cancelled: bool = False
    status: int = 0
    # 204：对方已通过 preview 收到内容，不需要再传文件。
    # 对文字消息来说这就是**成功**，不是拒绝（旧版误判为拒绝）。
    no_transfer: bool = False

    @property
    def is_declined(self) -> bool:
        return self.status == 403

    @property
    def is_pin_required(self) -> bool:
        return self.status == 401

    @property
    def is_busy(self) -> bool:
        return self.status == 409


def _base(peer: dict) -> str:
    proto = peer.get("protocol") or "https"
    host = peer.get("ip") or ""
    port = int(peer.get("port") or 53317)
    return f"{proto}://{host}:{port}{API}"


def _describe(peer: dict) -> str:
    return f"{peer.get('alias') or '?'} @ {peer.get('ip')}:{peer.get('port')}"


def _post_json(peer: dict, path: str, obj: dict, timeout: float = 30.0) -> tuple[int, dict]:
    url = _base(peer) + path
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    trace("C->S", f"POST {path} -> {_describe(peer)} ({len(data)}B)")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
            body = json.loads(raw) if raw else {}
            trace("S->C", f"{resp.status} {path} body={_snippet(body)}")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body_bytes = b""
        try:
            body_bytes = e.read()
        except Exception:
            pass
        parsed: dict = {}
        if body_bytes:
            try:
                parsed = json.loads(body_bytes)
            except Exception:
                parsed = {"raw": body_bytes.decode("utf-8", "replace")[:300]}
        trace("S->C", f"{e.code} {path} (HTTPError) body={_snippet(parsed)}")
        return e.code, parsed
    except Exception as e:
        trace("ERR", f"{path} -> {_describe(peer)} : {type(e).__name__}: {e}")
        raise


def _snippet(obj) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s[:400]


def register(peer: dict, my_info: dict, timeout: float = 8.0) -> tuple[bool, dict]:
    try:
        code, body = _post_json(peer, "/register", {"info": my_info}, timeout)
        return code == 200, body
    except Exception:
        return False, {}


def _safe_name(alias: str) -> str:
    """文件名里不能出现各系统的非法字符，否则对方可能拒收。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", alias or "LocalSend").strip()
    return name[:32] or "LocalSend"


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _text_preview(payload: bytes, limit: int = 512) -> str:
    """把消息正文转成 preview 字段。

    官方实现（send_controller.dart）对 FileType.text 是
        preview: utf8.decode(files.first.bytes!)
    即 **直接放明文**，接收端把它当字符串显示。这里若做 base64，
    对方就会把 base64 串原样显示出来 —— 这正是「内容和接收的不一致」的原因。

    截断按「字符」而不是「字节」，否则中文会被砍成半个码点变成乱码。
    """
    text = payload.decode("utf-8", errors="replace")
    return text if len(text) <= limit else text[:limit]


def _file_meta(fid: str, name: str, ftype: str, payload: bytes) -> dict:
    meta = {
        "id": fid,
        "fileName": name,
        "size": len(payload),
        "fileType": ftype,
        "sha256": _sha256_hex(payload),
    }
    if ftype == "text":
        # 文字消息：preview 即正文，接收端直接显示，无需再传文件
        meta["preview"] = _text_preview(payload)
    else:
        # 二进制文件：preview 只是缩略图/片段，base64 是协议约定
        meta["preview"] = base64.b64encode(payload[:512]).decode("ascii")
    return meta


def _upload_one(peer: dict, sid: str, fid: str, token: str, payload: bytes,
                timeout: float = 0.0) -> SendResult:
    # 必须 urlencode：token 是 base64，含 '+' '/' '=' 是常态。
    # 直接拼进 URL 时 '+' 会被服务端解码成空格（parse_qs 的规则），
    # token 从此对不上，对方回 403，而 403 又会被误读成「用户拒收」。
    qs = urllib.parse.urlencode(
        {"sessionId": sid, "fileId": fid, "token": token}, quote_via=urllib.parse.quote)
    url = f"{_base(peer)}/upload?{qs}"
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/octet-stream",
                 "Content-Length": str(len(payload))},
    )
    if timeout <= 0:
        # 按体积估一个合理上限：30s 起步，每 64KB 加 1s，最多 300s。
        # 原来固定 600s，一旦对方不响应，界面就会「发送中」卡十分钟。
        timeout = min(300.0, 30.0 + len(payload) / 65536.0)
    trace("C->S", f"POST /upload fid={fid[:8]} {len(payload)}B "
                  f"timeout={timeout:.0f}s -> {_describe(peer)}")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            resp.read()
            trace("S->C", f"{resp.status} /upload ok")
            return SendResult(True, status=resp.status)
    except urllib.error.HTTPError as e:
        body_txt = ""
        try:
            body_txt = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        trace("S->C", f"{e.code} /upload HTTPError body={body_txt}")
        if e.code == 403:
            low = body_txt.lower()
            # 403 有两种含义：用户点了拒绝，或 token/session 校验失败。
            # 混在一起会把协议错误说成「用户拒收」，让人误以为对方没点接受。
            if any(k in low for k in ("token", "session", "invalid", "not found",
                                      "malformed")):
                return SendResult(
                    False, f"对方校验未通过（403）：{body_txt or '会话或凭证无效'}", status=403)
            return SendResult(False, "对方取消了接收（403）", cancelled=True, status=403)
        if e.code == 409:
            return SendResult(False, "会话冲突（409），请稍后重试", status=409)
        return SendResult(False, f"上传失败 HTTP {e.code}", status=e.code)
    except Exception as e:
        trace("ERR", f"/upload : {type(e).__name__}: {e}")
        return SendResult(False, f"上传失败：{e}")


def prepare(peer: dict, my_info: dict, metas: dict[str, dict],
            pin: str = "", timeout: float = 20.0) -> tuple[bool, str, dict, str, int]:
    """返回 (是否被接受, sessionId, token表, 错误信息, HTTP状态码)。"""
    payload: dict = {"info": my_info, "files": metas}
    if pin:
        payload["pin"] = pin
    try:
        code, body = _post_json(peer, "/prepare-upload", payload, timeout)
    except Exception as e:
        return False, "", {}, f"连接失败：{e}", 0

    if code == 204:
        # 204 = Finished (No file transfer needed)。
        # 对方已经从 preview 里拿到内容，不需要再传文件 —— 对文字消息这就是送达。
        return True, "", {}, "", 204
    if code == 401:
        return False, "", {}, "需要 PIN，请在设置里填写对方显示的 PIN", 401
    if code == 409:
        return False, "", {}, "对方正在接收其他设备的文件（409），请稍后重试", 409
    if code == 403:
        return False, "", {}, "对方拒绝了该文件（403）", 403
    if code != 200:
        return False, "", {}, f"协商失败 HTTP {code} {_snippet(body)}", code

    sid = body.get("sessionId") or ""
    tokens = body.get("files") or {}
    if not sid:
        return False, "", {}, "对方未返回 sessionId", code
    return True, sid, tokens, "", code


def send_text(peer: dict, my_info: dict, text: str, alias: str,
              pin: str = "") -> SendResult:
    body = text.encode("utf-8")
    name = f"{_safe_name(alias)}-消息.txt"

    last: SendResult = SendResult(False, "未执行")
    # 每条消息用唯一 fileId；仅 409（会话冲突）时换 id 重试一次
    for attempt in range(2):
        fid = uuid.uuid4().hex
        meta = _file_meta(fid, name, "text", body)
        ok, sid, tokens, err, code = prepare(peer, my_info, {fid: meta}, pin=pin)

        if code == 204:
            # 对方已把 preview 当正文收下，不需要上传
            trace("OK", "prepare 204：对方已收到消息正文（无需上传文件）")
            return SendResult(True, "已送达", status=204, no_transfer=True)

        if not ok:
            last = SendResult(False, err, status=code)
            if code == 409 and attempt == 0:
                trace("RETRY", "prepare 409 会话冲突，换 fileId 重试一次")
                time.sleep(0.6)
                continue
            return last
        token = tokens.get(fid)
        if not token:
            # 某些实现对 token 表键名做了改写，退一步取第一个
            if len(tokens) == 1:
                token = next(iter(tokens.values()))
            else:
                return SendResult(False, "对方未授权该消息", status=code)
        return _upload_one(peer, sid, fid, token, body)

    return last


def send_files(peer: dict, my_info: dict, paths: list[str], pin: str = "",
               progress=None) -> list[tuple[str, SendResult]]:
    metas: dict[str, dict] = {}
    payloads: dict[str, bytes] = {}
    names: dict[str, str] = {}

    for p in paths:
        path = Path(p)
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except Exception as e:
            trace("ERR", f"读取 {p} 失败：{e}")
            continue
        fid = uuid.uuid4().hex
        ftype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        metas[fid] = _file_meta(fid, path.name, ftype, data)
        payloads[fid] = data
        names[fid] = path.name

    if not metas:
        return []

    ok, sid, tokens, err, code = prepare(peer, my_info, metas, pin=pin)
    if code == 204:
        # 文件场景下 204 表示对方没有接受传输（没有产生会话）
        msg = "对方未接收文件（204），请让对方在弹窗里点「接受」"
        return [(names[fid], SendResult(False, msg, status=204)) for fid in metas]
    if not ok:
        return [(names[fid], SendResult(False, err, status=code)) for fid in metas]

    results: list[tuple[str, SendResult]] = []
    for fid, meta in metas.items():
        token = tokens.get(fid)
        if not token and len(tokens) == 1:
            token = next(iter(tokens.values()))
        if not token:
            results.append((meta["fileName"], SendResult(False, "对方未授权该文件")))
            continue
        res = _upload_one(peer, sid, fid, token, payloads[fid])
        results.append((meta["fileName"], res))
        if progress:
            try:
                progress(meta["fileName"], res)
            except Exception:
                pass
        if res.cancelled:
            break
    return results


def cancel(peer: dict, sid: str) -> None:
    try:
        q = urllib.parse.urlencode({"sessionId": sid}, quote_via=urllib.parse.quote)
        _post_json(peer, f"/cancel?{q}", {}, timeout=6)
    except Exception:
        pass
