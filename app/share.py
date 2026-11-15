"""局域网链接分享：在本机起一个只说 HTTP 的小服务，把文件/文字变成手机能直接打开的网页。

为什么不复用 server.py 的 HTTPS 服务？
  1. 那个端口跑的是 LocalSend 协议（要证书、要 token、路径是 /api/localsend/*），
     手机浏览器直接访问会卡在自签证书警告上，体验极差；
  2. 分享页只需要 GET，纯 HTTP + 一个随机短码就够，安全性靠「码不可枚举」而不是证书。

所以这里单开一个端口（默认 53319），与协议层完全隔离，互不影响。
"""
from __future__ import annotations

import html
import mimetypes
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from .config import local_ip

SHARE_PORT = 53319

# 单次最多带多少个文件，避免页面被塞爆
MAX_ITEMS = 200


def _fmt_size(n: int) -> str:
    try:
        n = float(n)
    except Exception:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return (f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}")
        n /= 1024
    return f"{n:.1f} GB"


def _esc(s: str) -> str:
    return html.escape(str(s or ""), quote=True)


class ShareItem:
    """一个被分享的文件。"""

    __slots__ = ("sid", "path", "name", "size", "mime")

    def __init__(self, path: Path, name: str = "") -> None:
        self.sid = uuid.uuid4().hex[:12]
        self.path = Path(path)
        self.name = (name or self.path.name).strip() or "file"
        try:
            self.size = self.path.stat().st_size
        except Exception:
            self.size = 0
        self.mime = mimetypes.guess_type(self.name)[0] or "application/octet-stream"

    @property
    def is_image(self) -> bool:
        return self.mime.startswith("image/")


class ShareServer:
    """分享服务：线程内跑 ThreadingHTTPServer，UI 只需要调 start/stop/url。"""

    def __init__(self) -> None:
        self.items: list[ShareItem] = []
        self._by_id: dict[str, ShareItem] = {}
        self.text: str = ""
        self.title: str = "LocalSend Chat 文件分享"
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self.port: int = 0
        self.last_error: str = ""

    # ---------- 内容 ----------
    @property
    def running(self) -> bool:
        return self._httpd is not None

    def add_paths(self, paths) -> int:
        """加入文件；返回真正新增的数量（重复路径跳过）。"""
        added = 0
        with self._lock:
            exists = {str(it.path) for it in self.items}
            for p in paths:
                try:
                    ap = Path(p)
                    if not ap.is_file() or str(ap) in exists:
                        continue
                except Exception:
                    continue
                if len(self.items) >= MAX_ITEMS:
                    break
                it = ShareItem(ap)
                self.items.append(it)
                self._by_id[it.sid] = it
                exists.add(str(ap))
                added += 1
        return added

    def remove(self, sid: str) -> None:
        with self._lock:
            it = self._by_id.pop(sid, None)
            if it is not None:
                try:
                    self.items.remove(it)
                except ValueError:
                    pass

    def clear(self) -> None:
        with self._lock:
            self.items.clear()
            self._by_id.clear()
            self.text = ""

    def find(self, sid: str) -> ShareItem | None:
        with self._lock:
            return self._by_id.get(sid)

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        if self._httpd is not None:
            return True
        handler = _make_handler(self)
        last: Exception | None = None
        for port in (SHARE_PORT, SHARE_PORT + 1, SHARE_PORT + 2, 0):
            try:
                httpd = ThreadingHTTPServer(("", port), handler)
                httpd.daemon_threads = True
                self._httpd = httpd
                self.port = httpd.server_address[1]
                self._thread = threading.Thread(
                    target=httpd.serve_forever, name="share-http", daemon=True)
                self._thread.start()
                self.last_error = ""
                return True
            except Exception as e:      # 端口被占用等
                last = e
                continue
        self.last_error = str(last or "端口不可用")
        return False

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        self._thread = None

    def url(self, sid: str = "") -> str:
        """给手机用的地址。sid 为空时是首页。"""
        if not self.running:
            return ""
        ip = local_ip()
        base = f"http://{ip}:{self.port}/"
        return base + (f"#f-{sid}" if sid else "")


# ---------------------------------------------------------------- 页面
_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0e1116">
<title>{title}</title>
<style>
:root{{--bg:#0e1116;--card:#171a22;--line:#262b36;--fg:#e8ecf3;--dim:#9aa4b5;--time:#6b7484;--gold:#d9a441;}}
*{{box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
body{{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei UI",sans-serif;
padding:18px 14px 40px;}}
.head{{display:flex;align-items:center;gap:11px;margin:4px 2px 18px}}
.logo{{width:38px;height:38px;border-radius:11px;flex:0 0 38px;
background:linear-gradient(145deg,#f6d98a,#d9a441 46%,#9a6d18);
box-shadow:0 5px 14px rgba(217,164,65,.34),inset 0 1px 0 rgba(255,255,255,.5);
display:flex;align-items:center;justify-content:center;
font-weight:700;color:#3a2a08;font-size:19px}}
.title{{font-size:17px;font-weight:600;letter-spacing:.2px}}
.sub{{font-size:12px;color:var(--time);margin-top:1px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:15px;
padding:5px 0;margin-bottom:14px;overflow:hidden}}
.row{{display:flex;align-items:center;gap:12px;padding:12px 14px;
border-bottom:1px solid var(--line);text-decoration:none;color:inherit}}
.row:last-child{{border-bottom:0}}
.row:active{{background:#1f2430}}
.ico{{width:40px;height:40px;flex:0 0 40px;border-radius:10px;background:#232936;
display:flex;align-items:center;justify-content:center;font-size:18px}}
.meta{{flex:1;min-width:0}}
.nm{{font-size:14.5px;font-weight:500;word-break:break-all;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.sz{{font-size:12px;color:var(--time);margin-top:3px}}
.dl{{flex:0 0 auto;font-size:13px;color:#10141a;background:var(--gold);
padding:7px 15px;border-radius:999px;font-weight:600}}
.txt{{background:var(--card);border:1px solid var(--line);border-radius:15px;
padding:15px;margin-bottom:14px;white-space:pre-wrap;word-break:break-word;
font-size:15px;line-height:1.65}}
.bar{{display:flex;gap:10px;margin-bottom:16px}}
.btn{{flex:1;text-align:center;padding:11px;border-radius:12px;border:1px solid var(--line);
background:var(--card);color:var(--fg);font-size:14px;font-weight:500;cursor:pointer}}
.btn.gold{{background:var(--gold);color:#10141a;border-color:var(--gold);font-weight:600}}
.thumb{{width:100%;border-radius:12px;margin-top:8px;display:block}}
.foot{{text-align:center;color:var(--time);font-size:12px;margin-top:22px;line-height:1.8}}
.tip{{color:var(--gold);font-size:12.5px;text-align:center;margin:10px 0 16px}}
</style></head><body>
<div class="head">
  <div class="logo">◈</div>
  <div><div class="title">{title}</div><div class="sub">{sub}</div></div>
</div>
{body}
<div class="foot">LocalSend Chat · 局域网直连<br>关闭电脑上的分享后，此链接即失效</div>
<script>
document.addEventListener('click',function(e){{
  var b=e.target.closest('[data-copy]');if(!b)return;
  var t=document.getElementById(b.getAttribute('data-copy'));
  if(!t)return;
  var s=t.innerText;
  try{{
    if(navigator.clipboard){{navigator.clipboard.writeText(s);}}
    else{{var a=document.createElement('textarea');a.value=s;document.body.appendChild(a);
      a.select();document.execCommand('copy');document.body.removeChild(a);}}
    var o=b.innerText;b.innerText='已复制';setTimeout(function(){{b.innerText=o;}},1400);
  }}catch(err){{}}
}});
</script>
</body></html>
"""


def _page_html(share: ShareServer, host: str) -> tuple[str, str]:
    """返回 (title, html)。"""
    items = list(share.items)
    text = (share.text or "").strip()
    title = share.title or "LocalSend Chat 文件分享"

    total = sum(it.size for it in items)
    bits = []
    if items:
        bits.append(f"{len(items)} 个文件 · {_fmt_size(total)}")
    if text and not items:
        bits.append("一条文字消息")
    sub = " · ".join(bits) or "暂无内容"

    out: list[str] = []
    if text:
        out.append('<div class="bar"><button class="btn gold" data-copy="t1">复制文字</button></div>')
        out.append(f'<div class="txt" id="t1">{_esc(text)}</div>')

    if items:
        out.append('<div class="card">')
        for it in items:
            icon = "🖼" if it.is_image else (
                "📄" if it.mime.startswith("text/") else (
                    "🎬" if it.mime.startswith("video/") else (
                        "🎵" if it.mime.startswith("audio/") else "📦")))
            href = f"/d/{it.sid}/{quote(it.name)}"
            out.append(
                f'<a class="row" href="{href}" download="{_esc(it.name)}">'
                f'<div class="ico">{icon}</div>'
                f'<div class="meta"><div class="nm">{_esc(it.name)}</div>'
                f'<div class="sz">{_fmt_size(it.size)}</div></div>'
                f'<div class="dl">下载</div></a>'
            )
        out.append('</div>')

    if not items and not text:
        out.append('<div class="card"><div class="row"><div class="ico">🕐</div>'
                   '<div class="meta"><div class="nm">电脑还没有添加任何内容</div>'
                   '<div class="sz">在电脑的「链接分享」窗口里添加文件即可</div></div></div></div>')

    if items:
        out.append('<div class="bar">'
                   f'<button class="btn" data-copy="u1">复制链接</button></div>')
        out.append(f'<div style="display:none" id="u1">{_esc(host)}</div>')

    return title, _PAGE.format(title=_esc(title), sub=_esc(sub), body="\n".join(out))


def _make_handler(share: ShareServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalSendShare/1.0"
        protocol_version = "HTTP/1.1"

        # 静默日志：局域网工具不需要刷控制台
        def log_message(self, fmt, *args):
            pass

        def _send_html(self, code: int, body: str) -> None:
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except Exception:
                pass

        def _send_text(self, code: int, msg: str) -> None:
            raw = msg.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except Exception:
                pass

        def do_HEAD(self):
            self.do_GET(head=True)

        def do_GET(self, head: bool = False):
            path = unquote(urlparse(self.path).path)
            parts = [p for p in path.split("/") if p]

            # 首页
            if not parts or parts[0] in ("index.html", "s"):
                if parts and parts[0] == "s":
                    return self._send_html(404, "<h1>404</h1><p>分享首页请访问根路径。</p>")
                host = f"http://{self.headers.get('Host') or local_ip()}/"
                _t, body = _page_html(share, host)
                return self._send_html(200, body)

            # 下载
            if parts[0] in ("d", "v") and len(parts) >= 2:
                it = share.find(parts[1])
                if it is None:
                    return self._send_html(404, "<h1>404</h1><p>文件不存在或已取消分享。</p>")
                return self._stream(it, inline=(parts[0] == "v"), head=head)

            # 简单健康检查，方便排查
            if parts[0] == "ping":
                return self._send_text(200, "ok")

            return self._send_html(404, "<h1>404</h1><p>页面不存在。</p>")

        def _stream(self, it: ShareItem, inline: bool, head: bool = False) -> None:
            try:
                f = it.path.open("rb")
            except Exception:
                return self._send_html(404, "<h1>404</h1><p>文件已被移动或删除。</p>")
            with f:
                try:
                    size = it.path.stat().st_size
                except Exception:
                    size = it.size
                self.send_response(200)
                self.send_header("Content-Type", it.mime)
                self.send_header("Content-Length", str(size))
                disp = "inline" if inline else "attachment"
                self.send_header(
                    "Content-Disposition",
                    f"{disp}; filename*=UTF-8''{quote(it.name)}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if head:
                    return
                try:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                except Exception:
                    pass

    return Handler
