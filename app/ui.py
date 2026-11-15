"""聊天窗口（Tkinter）。协议层零改动，这里只是把 v2 的收发结果渲染成对话。

设计要点：
  1. 左侧会话列表 = 已知设备 + 最近消息预览；
  2. 右侧消息气泡 = 该设备的历史消息（来自 SQLite）；
  3. 底部输入框回车即发送，走 protocol.send_text，与官方客户端互通；
  4. 后台线程只往 Bus 里塞事件，UI 用 after() 轮询，避免跨线程操作控件。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import certs, imaging, protocol, share
from . import theme as _theme
from .config import Config, local_ip
from .discovery import DISCOVERY_PORT, Discoverer, probe_ip
from .server import Server
from .storage import Bus, Store
from .visibility import count_online, force_visible, split_visible

ASSETS = Path(__file__).resolve().parent / "assets"
LOGO_PNG = ASSETS / "logo-gold-512.png"
APP_ICO = ASSETS / "app.ico"


# ---------------- 主题 ----------------
# 这些是「当前主题」的全局槽位。所有绘制代码都读它们，
# 所以 apply_theme() 一换，整个界面立刻变色，不需要重建控件。
THEME_NAME  = _theme.DEFAULT
BG          = "#12141a"
BG_SIDE     = "#171a22"
BG_HOVER    = "#1f2430"
BG_SEL      = "#232936"
BG_INPUT    = "#1c2029"
FG          = "#e6e8ee"
FG_DIM      = "#8b93a7"
FG_TIME     = "#5d6478"
ACCENT      = "#3d7eff"
ACCENT_DARK = "#2f5cc0"
BUBBLE_OUT  = "#2f6bd8"
BUBBLE_OUT_FG = "#ffffff"
BUBBLE_IN   = "#252a35"
BUBBLE_IN_FG  = "#e6e8ee"
LINE        = "#242936"
OK_GREEN    = "#38c172"
WARN_ORANGE = "#e8a33d"
DANGER      = "#e05c5c"
IS_LIGHT    = False


def apply_theme(name: str) -> str:
    """把主题调色板写进全局槽位，并返回真正生效的名字。"""
    global THEME_NAME, BG, BG_SIDE, BG_HOVER, BG_SEL, BG_INPUT
    global FG, FG_DIM, FG_TIME, ACCENT, ACCENT_DARK
    global BUBBLE_OUT, BUBBLE_OUT_FG, BUBBLE_IN, BUBBLE_IN_FG
    global LINE, OK_GREEN, WARN_ORANGE, DANGER, IS_LIGHT

    t = _theme.get(name)
    THEME_NAME = name if name in _theme.THEMES else _theme.DEFAULT
    BG            = t["bg"]
    BG_SIDE       = t["side"]
    BG_HOVER      = t["hover"]
    BG_SEL        = t["sel"]
    BG_INPUT      = t["input"]
    FG            = t["fg"]
    FG_DIM        = t["dim"]
    FG_TIME       = t["time"]
    ACCENT        = t["accent"]
    ACCENT_DARK   = t["accent_h"]
    BUBBLE_OUT    = t["out_bg"]
    BUBBLE_OUT_FG = t["out_fg"]
    BUBBLE_IN     = t["in_bg"]
    BUBBLE_IN_FG  = t["in_fg"]
    LINE          = t["line"]
    OK_GREEN      = t["green"]
    WARN_ORANGE   = t["orange"]
    DANGER        = t["danger"]
    IS_LIGHT      = bool(t.get("is_light"))
    return THEME_NAME


def _measure_font(font_spec) -> tkfont.Font:
    return tkfont.Font(family=font_spec[0], size=font_spec[1],
                       weight=(font_spec[2] if len(font_spec) > 2 else "normal"))


def _wrap_text(text: str, font: tkfont.Font, max_w: int) -> list[str]:
    """按像素宽度折行。

    tkinter 的 Label 会自动折行，但圆角气泡要先用画布量出高度才能画形状，
    所以这里自己折行：中英混排都按「逐字累加 + 空格优先」处理，
    避免长串英文被从单词中间切开。
    """
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            if font.measure(cur + ch) <= max_w:
                cur += ch
                continue
            # 超宽：优先在最近的空格处断，避免把英文单词劈开
            cut = cur.rfind(" ")
            if cut > 0 and font.measure(cur[cut + 1:]) < max_w * 0.5:
                lines.append(cur[:cut])
                cur = cur[cut + 1:] + ch
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines or [""]


def _round_rect(cv: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                r: float, **kw):
    """用平滑多边形画圆角矩形（tkinter 没有原生圆角容器）。"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, splinesteps=24, **kw)

FONT_UI   = ("Microsoft YaHei UI", 10)
FONT_SM   = ("Microsoft YaHei UI", 9)
FONT_NAME = ("Microsoft YaHei UI", 10, "bold")
FONT_MSG  = ("Microsoft YaHei UI", 10)


def _fmt_time(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} GB"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config()

        # 主题必须在建任何控件之前确定：所有颜色都从全局槽位读取
        apply_theme(self.cfg.get("theme") or _theme.DEFAULT)

        self.store = Store()
        self.bus = Bus()
        # 上次异常退出可能留下「发送中」的孤儿记录，启动时统一收尾
        try:
            n = self.store.reset_stale_sending()
            if n:
                protocol.trace("INFO", f"启动清理残留 sending：{n} 条")
        except Exception:
            pass

        certs.ensure()
        self.my_fingerprint = certs.fingerprint()

        self.server = Server(self.cfg, self.store, self.bus, self._my_info)
        self.server.start()
        self.cfg.set("port", self.server.port)

        # 链接分享服务：与协议层端口隔离，按需启动
        self.share = share.ShareServer()
        self._share_dialog = None
        # v2.5.1：只有「屏蔽」才写持久化名单；「删除」不再写入，
        # 因此设备仍在线时下次刷新就会自然回来。
        try:
            self._blocked: set[str] = self.store.blocked_fingerprints()
        except Exception:
            self._blocked = set()

        self.discoverer = Discoverer(
            self._my_info,
            alias=self.cfg.alias,
            port=DISCOVERY_PORT,   # 发现端口固定，不随 HTTP 端口顺延
        )

        self.online: dict[str, dict] = {}       # fp -> 最新 info（含 ip）
        self.current_fp: str = ""
        self._bubbles: list[tk.Widget] = []
        self._images: list[tk.PhotoImage] = []       # 缩略图引用，防 GC
        self._thumb_cache: dict = {}

        self._apply_window_title()
        self.geometry("1040x700")
        self.minsize(860, 560)
        self.configure(bg=BG)
        self._set_icon()

        self._build()
        self._load_conversations()
        self._tick()

        self.discoverer.start(self._on_peer)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- 自身信息 ----------------
    def _my_info(self) -> dict:
        return {
            "alias": self.cfg.alias,
            "version": "2.1",
            "deviceModel": self.cfg.get("device_model") or "LocalSend Chat",
            "deviceType": "desktop",
            "fingerprint": self.my_fingerprint,
            "port": self.cfg.port,
            "protocol": "https",
            "download": False,
        }

    # ---------------- 窗口标题 / 关于 / 屏蔽 ----------------
    def _apply_window_title(self) -> None:
        from . import __version__
        self.title(f"{self.cfg.panel_title} v{__version__} · {self.cfg.alias}")

    def _on_title_double_click(self, event=None) -> None:
        """双击版本号八次 -> 校验管理口令 -> 修改面板名称。"""
        now = time.time()
        if now - getattr(self, "_title_click_ts", 0.0) > 6.0:
            self._title_clicks = 0
        self._title_click_ts = now
        self._title_clicks = getattr(self, "_title_clicks", 0) + 1
        if self._title_clicks < 8:
            return
        self._title_clicks = 0
        pwd = simpledialog.askstring("管理员验证", "请输入管理口令：",
                                     show="*", parent=self)
        if pwd is None:
            return
        if pwd != self.cfg.admin_pass:
            messagebox.showerror("验证失败", "口令不正确。", parent=self)
            return
        new = simpledialog.askstring(
            "修改面板名称", "输入新的面板名称（留空恢复默认）：",
            initialvalue=self.cfg.panel_title, parent=self)
        if new is None:
            return
        self.cfg.panel_title = new
        try:
            self.lbl_brand.configure(text=self.cfg.panel_title)
        except Exception:
            pass
        self._apply_window_title()
        self.lbl_status.configure(text="面板名称已更新", fg=OK_GREEN)

    def _open_about(self) -> None:
        AboutDialog(self)

    def _block_peer(self, row: dict) -> None:
        """屏蔽：写独立名单，可在「已屏蔽」里逐个或一键恢复。"""
        fp = row["fp"]
        alias = row.get("alias") or "该设备"
        if not messagebox.askyesno(
                "屏蔽设备",
                f"确定要屏蔽「{alias}」吗？\n\n"
                "屏蔽后点「刷新设备」也不会再出现，但可在左下角\n"
                "「已屏蔽」中恢复。聊天记录会保留。\n\n"
                "只想暂时移出列表请用「删除该设备」。",
                parent=self):
            return
        try:
            self.store.block_peer(fp, alias)
            self._blocked = self.store.blocked_fingerprints()
        except Exception as e:
            messagebox.showerror("屏蔽失败", str(e), parent=self)
            return
        self.online.pop(fp, None)
        if self.current_fp == fp:
            self.current_fp = ""
            self._clear_bubbles()
            self._restore_header()
        self._reload_keep_selection()
        self.lbl_status.configure(
            text=f"已屏蔽 {alias} · 可在「已屏蔽」中恢复", fg=OK_GREEN)

    def _unblock_peer(self, fp: str) -> None:
        try:
            self.store.unblock_peer(fp)
            self._blocked = self.store.blocked_fingerprints()
        except Exception as e:
            messagebox.showerror("恢复失败", str(e), parent=self)
            return
        self._reload_keep_selection()
        self.lbl_status.configure(text="已恢复该设备，点「刷新设备」即可看到",
                                  fg=OK_GREEN)

    def _clear_blocked(self) -> None:
        if not self._blocked:
            self.lbl_status.configure(text="屏蔽列表本来就是空的", fg=FG_DIM)
            return
        if not messagebox.askyesno(
                "清空屏蔽列表",
                f"确定要恢复全部 {len(self._blocked)} 台被屏蔽的设备吗？",
                parent=self):
            return
        try:
            self.store.clear_blocked()
            self._blocked = set()
        except Exception as e:
            messagebox.showerror("清空失败", str(e), parent=self)
            return
        self._reload_keep_selection()
        self.lbl_status.configure(text="屏蔽列表已清空", fg=OK_GREEN)

    def _open_blocked(self) -> None:
        BlockedDialog(self)

    # ---------------- 布局 ----------------
    def _set_icon(self):
        """窗口图标用官方 LocalSend 图形的金属金版本。"""
        try:
            if APP_ICO.exists():
                self.iconbitmap(default=str(APP_ICO))
                return
        except Exception:
            pass
        try:
            self.iconbitmap(default="")
        except Exception:
            pass

    def _logo_photo(self, size: int):
        """把金色 logo 取成 Tk 图片，失败返回 None（降级到色块 + 字符）。"""
        key = f"_logo_{size}"
        cached = getattr(self, key, None)
        if cached is not None:
            return cached
        if not imaging.has_pil() or not LOGO_PNG.exists():
            return None
        raw = imaging.icon_png(LOGO_PNG, size, bg=BG_SIDE)
        if not raw:
            return None
        try:
            img = tk.PhotoImage(data=raw)
        except Exception:
            return None
        setattr(self, key, img)      # 必须持引用，否则会被 GC 掉变空白
        return img

    def _chip(self, parent, text, cmd, *, kind="ghost", font=None, padx=13, pady=6) -> tk.Label:
        """胶囊按钮。

        用 Label 而非 Button：Windows 原生 Button 的边框/背景在 flat 下仍有
        系统描边，Label 才能做到真正的圆角观感与精确配色。
        """
        if kind == "primary":
            bg, fg, hov = ACCENT, "white", ACCENT_DARK
        elif kind == "solid":
            bg, fg, hov = BG_HOVER, FG, BG_SEL
        else:
            bg, fg, hov = BG_SIDE, FG_DIM, BG_HOVER

        lb = tk.Label(parent, text=text, bg=bg, fg=fg, font=font or FONT_SM,
                      padx=padx, pady=pady, cursor="hand2")
        lb.bind("<Button-1>", lambda e: cmd())
        lb.bind("<Enter>", lambda e: lb.configure(bg=hov, fg=FG if kind != "primary" else "white"))
        lb.bind("<Leave>", lambda e: lb.configure(bg=bg, fg=fg))
        return lb

    def _build(self) -> None:
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True)

        # ---------------- 顶栏 ----------------
        top = tk.Frame(root, bg=BG_SIDE, height=58)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)

        brand = tk.Frame(top, bg=BG_SIDE)
        brand.pack(side="left", padx=(16, 0))

        # 品牌标记：官方 LocalSend 图形的金属金版本
        logo_img = self._logo_photo(26)
        if logo_img is not None:
            tk.Label(brand, image=logo_img, bg=BG_SIDE, bd=0,
                     highlightthickness=0).pack(side="left", pady=16)
        else:
            # 没有 Pillow 时降级：金色方块 + 字符，保证 logo 位置不空
            box = tk.Frame(brand, bg="#d9a441", width=26, height=26)
            box.pack(side="left", pady=16)
            box.pack_propagate(False)
            tk.Label(box, text="◈", bg="#d9a441", fg="#3a2a08",
                     font=("Microsoft YaHei UI", 13, "bold")).pack(expand=True)

        self.lbl_brand = tk.Label(brand, text=self.cfg.panel_title, bg=BG_SIDE, fg=FG,
                                  font=("Microsoft YaHei UI", 12, "bold"),
                                  cursor="hand2")
        self.lbl_brand.pack(side="left", padx=(9, 10))
        self.lbl_brand.bind("<Double-Button-1>", self._on_title_double_click)
        from . import __version__ as _ver
        self.lbl_version = tk.Label(brand, text=f"v{_ver}", bg=BG_SIDE, fg=FG_TIME,
                                    font=("Consolas", 9), cursor="hand2")
        self.lbl_version.pack(side="left", padx=(0, 10))
        self.lbl_version.bind("<Double-Button-1>", self._on_title_double_click)
        tk.Label(brand, text=f"本机 · {self.cfg.alias}", bg=BG_SIDE, fg=FG_TIME,
                 font=FONT_SM).pack(side="left")

        # 右侧操作区：状态灯 + 胶囊按钮
        self.lbl_status = tk.Label(top, text="●  就绪", bg=BG_SIDE, fg=OK_GREEN, font=FONT_SM)
        self.lbl_status.pack(side="right", padx=(4, 16))

        for text, cmd in (("关于", self._open_about),
                          ("设置", self._open_settings),
                          ("诊断", self._open_diag),
                          ("添加设备", self._open_add_peer),
                          ("刷新设备", self._refresh)):
            self._chip(top, text, cmd).pack(side="right", padx=3, pady=14)

        # 链接分享：顶栏最左的强调按钮（pack 顺序决定最左侧）
        self._chip(top, "⤴  链接分享", self._open_share, kind="primary",
                   padx=15).pack(side="right", padx=(3, 8), pady=14)

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)

        # ---------------- 左侧 ----------------
        side = tk.Frame(body, bg=BG_SIDE, width=290)
        side.pack(fill="y", side="left")
        side.pack_propagate(False)

        side_head = tk.Frame(side, bg=BG_SIDE)
        side_head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(side_head, text="设备与对话", bg=BG_SIDE, fg=FG,
                 font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(side="left")
        # 在线设备计数徽标
        self.lbl_count = tk.Label(side_head, text="", bg=BG_HOVER, fg=FG_DIM,
                                  font=("Microsoft YaHei UI", 8), padx=7, pady=1)
        self.lbl_count.pack(side="right")

        side_wrap = tk.Frame(side, bg=BG_SIDE)
        side_wrap.pack(fill="both", expand=True, padx=(8, 0), pady=(0, 4))

        self.side_canvas = tk.Canvas(side_wrap, bg=BG_SIDE, highlightthickness=0, bd=0)
        self.side_scroll = tk.Scrollbar(side_wrap, orient="vertical",
                                        command=self.side_canvas.yview, width=8,
                                        bg=BG_SIDE, troughcolor=BG_SIDE, bd=0,
                                        relief="flat", activebackground=LINE)
        self.side_inner = tk.Frame(self.side_canvas, bg=BG_SIDE)
        self._side_win = self.side_canvas.create_window(
            (0, 0), window=self.side_inner, anchor="nw")
        self.side_canvas.configure(yscrollcommand=self.side_scroll.set)
        self.side_canvas.pack(side="left", fill="both", expand=True)
        self.side_scroll.pack(side="right", fill="y")
        self.side_inner.bind(
            "<Configure>",
            lambda e: self.side_canvas.configure(
                scrollregion=self.side_canvas.bbox("all")),
        )
        self.side_canvas.bind(
            "<Configure>",
            lambda e: self.side_canvas.itemconfigure(self._side_win, width=e.width),
        )
        self._row_widgets: dict[str, tk.Frame] = {}

        # 底部：网络信息 + 主题切换
        foot = tk.Frame(side, bg=BG_SIDE)
        foot.pack(fill="x", padx=16, pady=(6, 12))

        tk.Frame(side, bg=LINE, height=1).pack(fill="x", padx=12, pady=(0, 10))

        self.lbl_net = tk.Label(foot, bg=BG_SIDE, fg=FG_TIME, font=("Consolas", 8),
                                text=f":{self.cfg.port}  {local_ip()}", anchor="w")
        self.lbl_net.pack(fill="x")

        theme_row = tk.Frame(foot, bg=BG_SIDE)
        theme_row.pack(fill="x", pady=(7, 0))
        self.lbl_theme = tk.Label(
            theme_row, text=f"◑  {_theme.label(THEME_NAME)}", bg=BG_SIDE,
            fg=FG_DIM, font=FONT_SM, cursor="hand2", anchor="w")
        self.lbl_theme.pack(side="left")
        self.lbl_theme.bind("<Button-1>", lambda e: self._open_theme())
        self.lbl_theme.bind("<Enter>", lambda e: self.lbl_theme.configure(fg=ACCENT))
        self.lbl_theme.bind("<Leave>", lambda e: self.lbl_theme.configure(fg=FG_DIM))

        blocked_row = tk.Frame(foot, bg=BG_SIDE)
        blocked_row.pack(fill="x", pady=(5, 0))
        self.lbl_blocked = tk.Label(
            blocked_row, text="已屏蔽 (0)", bg=BG_SIDE, fg=FG_TIME,
            font=FONT_SM, cursor="hand2", anchor="w")
        self.lbl_blocked.pack(side="left")
        self.lbl_blocked.bind("<Button-1>", lambda e: self._open_blocked())
        self.lbl_blocked.bind("<Enter>", lambda e: self.lbl_blocked.configure(fg=ACCENT))
        self.lbl_blocked.bind("<Leave>", lambda e: self.lbl_blocked.configure(fg=FG_TIME))

        # ---------------- 右侧 ----------------
        right = tk.Frame(body, bg=BG)
        right.pack(fill="both", expand=True, side="left")

        head = tk.Frame(right, bg=BG, height=62)
        head.pack(fill="x")
        head.pack_propagate(False)

        self.head_avatar = tk.Label(head, text="", bg=BG_SEL, fg=FG_DIM,
                                    font=("Microsoft YaHei UI", 12, "bold"),
                                    width=2, height=1)
        self.head_avatar.pack(side="left", padx=(20, 10), pady=14)

        htext = tk.Frame(head, bg=BG)
        htext.pack(side="left", pady=14)
        self.lbl_title = tk.Label(htext, text="选择左侧设备开始对话", bg=BG, fg=FG,
                                  font=("Microsoft YaHei UI", 11, "bold"), anchor="w")
        self.lbl_title.pack(anchor="w")
        self.lbl_sub = tk.Label(htext, text="", bg=BG, fg=FG_DIM, font=FONT_SM, anchor="w")
        self.lbl_sub.pack(anchor="w", pady=(1, 0))

        tk.Frame(right, bg=LINE, height=1).pack(fill="x")

        wrap = tk.Frame(right, bg=BG)
        wrap.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        self.scroll = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview,
                                   width=9, bg=BG, troughcolor=BG, bd=0,
                                   relief="flat", activebackground=LINE)
        self.msg_frame = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.msg_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.msg_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._win, width=e.width),
        )
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        # ---------------- 输入区 ----------------
        bottom = tk.Frame(right, bg=BG)
        bottom.pack(fill="x", padx=20, pady=(8, 16))

        bar = tk.Frame(bottom, bg=BG)
        bar.pack(fill="x", pady=(0, 8))

        self._chip(bar, "＋  发送文件", self._pick_files, kind="solid",
                   padx=12, pady=5).pack(side="left")
        self._chip(bar, "下载目录", self._open_downloads, kind="solid",
                   padx=12, pady=5).pack(side="left", padx=7)

        self._chip(bar, "群发", self._open_group, kind="solid",
                   padx=12, pady=5).pack(side="left")

        self.lbl_hint = tk.Label(bar, text="回车发送 · Shift+回车换行", bg=BG, fg=FG_TIME,
                                 font=FONT_SM)
        self.lbl_hint.pack(side="right")

        # 输入框：外层 Frame 当 1px 描边，内层当底色。
        # 不用 highlightthickness —— 它和 Text 自带的高亮框会叠出两层亮边
        # （Windows 上那条 217,217,217 的灰线就是这么来的）。
        self.box = tk.Frame(bottom, bg=LINE)
        self.box.pack(fill="x")

        inner = tk.Frame(self.box, bg=BG_INPUT)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self._box_inner = inner

        self.btn_send = tk.Label(inner, text="发送", bg=ACCENT, fg="white",
                                 font=("Microsoft YaHei UI", 10, "bold"),
                                 padx=22, pady=7, cursor="hand2")
        self.btn_send.pack(side="right", padx=(8, 8), pady=8)
        self.btn_send.bind("<Button-1>", lambda e: self._send_text())
        self.btn_send.bind("<Enter>", lambda e: self.btn_send.configure(bg=ACCENT_DARK))
        self.btn_send.bind("<Leave>", lambda e: self.btn_send.configure(bg=ACCENT))

        self.text = tk.Text(inner, height=3, width=1, bg=BG_INPUT, fg=FG, font=FONT_MSG,
                            relief="flat", bd=0, highlightthickness=0,
                            insertbackground=FG, wrap="word", padx=10, pady=10)
        self.text.pack(side="left", fill="both", expand=True)
        self.text.bind("<Return>", self._on_return)
        self.text.bind("<Shift-Return>", lambda e: None)
        # 聚焦时整圈描边变强调色，给出明确的输入焦点反馈
        self.text.bind("<FocusIn>", lambda e: self.box.configure(bg=ACCENT))
        self.text.bind("<FocusOut>", lambda e: self.box.configure(bg=LINE))

    # ---------------- 会话列表 ----------------
    def _load_conversations(self) -> None:
        self._rows: list[tuple[str, dict]] = []

        # 刷新与手动添加共用同一套可见性判断；只有「屏蔽」会过滤设备。
        on_list, off_list = split_visible(
            self.online, self.store.peers(), self._blocked, self.my_fingerprint)

        for fp, info in on_list:
            self._rows.append(("online", {
                "fp": fp,
                "alias": info.get("alias") or "未知设备",
                "preview": self._last_message(fp),
                "online": True,
                "info": info,
            }))

        for fp, p in off_list:
            self._rows.append(("offline", {
                "fp": fp,
                "alias": p.get("alias") or "未知设备",
                "preview": self._last_message(fp),
                "online": False,
                "info": {"ip": p.get("ip", ""), "port": p.get("port", 0),
                         "protocol": p.get("protocol", ""),
                         "deviceModel": p.get("device", ""),
                         "alias": p.get("alias", "")},
            }))

        self._rows.sort(key=lambda r: (not r[1]["online"], -(self._preview_ts(r[1]["fp"]))))
        self._render_sidebar()

        # 没有选中项时自动打开第一个会话，避免右侧空着
        if self._rows and not self.current_fp:
            self._open_row(self._rows[0][1])

    def _render_sidebar(self) -> None:
        for w in self.side_inner.winfo_children():
            w.destroy()
        self._row_widgets.clear()

        # 计数必须基于过滤后的 _rows，永远等于列表里数得出来的数量
        online_n = count_online([r for _k, r in self._rows])
        try:
            self.lbl_count.configure(
                text=f"{online_n} 在线" if online_n else f"{len(self._rows)} 个对话")
        except Exception:
            pass
        try:
            self.lbl_blocked.configure(text=f"已屏蔽 ({len(self._blocked)})")
        except Exception:
            pass

        if not self._rows:
            empty = tk.Frame(self.side_inner, bg=BG_SIDE)
            empty.pack(fill="x", padx=14, pady=18)
            tk.Label(empty, text="◎", bg=BG_SIDE, fg=LINE,
                     font=("Microsoft YaHei UI", 22)).pack(pady=(0, 6))
            tk.Label(empty, text="还没有设备", bg=BG_SIDE, fg=FG_DIM,
                     font=FONT_SM).pack()
            tk.Label(empty, text="点「刷新设备」搜索，\n或用「添加设备」填手机 IP",
                     bg=BG_SIDE, fg=FG_TIME, font=("Microsoft YaHei UI", 8),
                     justify="center").pack(pady=(4, 0))
            return

        for _kind, row in self._rows:
            self._make_row(row)

    def _make_row(self, row: dict) -> None:
        fp = row["fp"]
        selected = fp == self.current_fp
        bg = BG_SEL if selected else BG_SIDE

        box = tk.Frame(self.side_inner, bg=bg, cursor="hand2")
        box.pack(fill="x", padx=6, pady=2)

        inner = tk.Frame(box, bg=bg)
        inner.pack(fill="x", padx=8, pady=7)

        # 头像：首字 + 在线圆点叠加在右下
        av = tk.Frame(inner, bg=bg, width=36, height=36)
        av.pack(side="left")
        av.pack_propagate(False)

        ch = (row["alias"] or "?").strip()[:1].upper() or "?"
        av_box = tk.Frame(av, bg=ACCENT if row["online"] else BG_HOVER,
                          width=36, height=36)
        av_box.pack()
        av_box.pack_propagate(False)
        av_lbl = tk.Label(av_box, text=ch, bg=ACCENT if row["online"] else BG_HOVER,
                          fg="white" if row["online"] else FG_TIME,
                          font=("Microsoft YaHei UI", 12, "bold"))
        av_lbl.pack(expand=True)

        text_col = tk.Frame(inner, bg=bg)
        text_col.pack(side="left", fill="x", expand=True, padx=(10, 0))

        line1 = tk.Frame(text_col, bg=bg)
        line1.pack(fill="x")

        name = tk.Label(line1, text=row["alias"], bg=bg,
                        fg=FG if row["online"] else FG_DIM,
                        font=FONT_NAME, anchor="w")
        name.pack(side="left")

        if row["online"]:
            tk.Label(line1, text="●", bg=bg, fg=OK_GREEN,
                     font=("Consolas", 7)).pack(side="right", padx=(4, 0))

        prev_text = row["preview"] or ("在线，可开始对话" if row["online"] else "暂无消息")
        clip = prev_text[:16] + ("…" if len(prev_text) > 16 else "")
        prev = tk.Label(text_col, text=clip, bg=bg, fg=FG_TIME, font=FONT_SM,
                        anchor="w", justify="left")
        prev.pack(fill="x", pady=(2, 0))

        for w in (box, inner, av, av_box, av_lbl, text_col, line1, name, prev):
            w.bind("<Button-1>", lambda e, r=row: self._open_row(r))
            w.bind("<Button-3>", lambda e, r=row: self._row_menu(e, r))
            w.bind("<Enter>", lambda e, b=box, f=fp: self._row_hover(b, f, True))
            w.bind("<Leave>", lambda e, b=box, f=fp: self._row_hover(b, f, False))

        self._row_widgets[fp] = box

    def _row_menu(self, event, row: dict) -> None:
        """会话行右键菜单：删除 / 屏蔽 / 清空记录 / 复制 IP。"""
        m = tk.Menu(self, tearoff=0, bg=BG_HOVER, fg=FG,
                    activebackground=ACCENT, activeforeground="white",
                    bd=0, font=FONT_SM)
        m.add_command(label="  删除该设备（刷新后可再出现）  ",
                      command=lambda: self._delete_peer(row))
        m.add_command(label="  屏蔽该设备  ",
                      command=lambda: self._block_peer(row))
        m.add_command(label="  清空聊天记录  ",
                      command=lambda: self._clear_history(row["fp"]))
        info = row.get("info") or {}
        ip = info.get("ip") or ""
        if ip:
            m.add_separator()
            m.add_command(label=f"  复制 IP：{ip}  ",
                          command=lambda: self._copy(ip))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _copy(self, text: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.lbl_status.configure(text="已复制到剪贴板", fg=OK_GREEN)
        except Exception:
            pass

    def _delete_peer(self, row: dict) -> None:
        """删除设备。

        这里必须带「是否清空聊天记录」的选择——用户删设备往往只是想清列表，
        直接连记录一起删会让人措手不及。
        """
        fp = row["fp"]
        alias = row.get("alias") or "该设备"
        ans = messagebox.askyesnocancel(
            "删除设备",
            f"确定从列表移除「{alias}」吗？\n\n"
            "删除不会拉黑对方：下次点「刷新设备」或对方发消息时，\n"
            "只要它还在线就会重新出现。想让它永远不出现，\n"
            "请用右键菜单的「屏蔽该设备」。\n\n"
            "选「是」：移除并清空聊天记录\n"
            "选「否」：仅移除，保留聊天记录\n"
            "选「取消」：什么也不做",
            parent=self)
        if ans is None:
            return
        try:
            # 关键：只删会话行（可选连带消息），绝不写任何持久化名单
            self.store.delete_peer(fp, drop_history=bool(ans))
        except Exception as e:
            messagebox.showerror("删除失败", str(e), parent=self)
            return
        self.online.pop(fp, None)
        if self.current_fp == fp:
            self.current_fp = ""
            self._clear_bubbles()
            self._restore_header()
            try:
                self.lbl_sub.configure(text="")
            except Exception:
                pass
        self._reload_keep_selection()
        self.lbl_status.configure(
            text=f"已移除 {alias} · 刷新后可重新出现", fg=OK_GREEN)

    def _clear_history(self, fp: str) -> None:
        if not messagebox.askyesno("清空聊天记录",
                                   "确定要清空该设备的全部聊天记录吗？此操作不可撤销。",
                                   parent=self):
            return
        try:
            self.store.clear_history(fp)
        except Exception as e:
            messagebox.showerror("清空失败", str(e), parent=self)
            return
        if self.current_fp == fp:
            self._render_history()
        self._reload_keep_selection()
        self.lbl_status.configure(text="聊天记录已清空", fg=OK_GREEN)

    def _row_hover(self, box: tk.Frame, fp: str, entering: bool) -> None:
        if fp == self.current_fp:
            return
        color = BG_HOVER if entering else BG_SIDE

        def paint(w):
            try:
                # 头像色块与强调色小圆点不跟随悬停，否则会闪烁
                if w.cget("bg") in (ACCENT, OK_GREEN):
                    return
                w.configure(bg=color)
            except Exception:
                pass
            for c in w.winfo_children():
                paint(c)

        paint(box)

    def _last_message(self, fp: str) -> str:
        hist = self.store.history(fp, limit=1)
        if not hist:
            return ""
        m = hist[-1]
        prefix = "我：" if m["direction"] == "out" else ""
        if m["kind"] == "file":
            return f"{prefix}[文件] {m['body']}"
        return prefix + m["body"].replace("\n", " ")

    def _preview_ts(self, fp: str) -> float:
        hist = self.store.history(fp, limit=1)
        return hist[-1]["ts"] if hist else 0.0

    def _open_row(self, row: dict) -> None:
        self.current_fp = row["fp"]
        self._render_sidebar()
        self._restore_header()
        sub = row["info"].get("deviceModel") or ""
        ip = row["info"].get("ip") or ""
        state = "在线" if row["online"] else "离线（仅历史）"
        bits = [state]
        if sub:
            bits.append(sub)
        if ip:
            bits.append(ip)
        self.lbl_sub.configure(text=" · ".join(bits))
        self._render_history()

    # ---------------- 消息渲染 ----------------
    def _clear_bubbles(self) -> None:
        for w in self._bubbles:
            try:
                w.destroy()
            except Exception:
                pass
        self._bubbles.clear()

    def _render_history(self) -> None:
        self._clear_bubbles()
        if not self.current_fp:
            return
        hist = self.store.history(self.current_fp, limit=self.cfg.history_limit)
        if not hist:
            tip = tk.Label(self.msg_frame, text="还没有消息，说点什么吧",
                           bg=BG, fg=FG_TIME, font=FONT_SM)
            tip.pack(pady=40)
            self._bubbles.append(tip)
            return
        last_day = ""
        for m in hist:
            day = time.strftime("%Y-%m-%d", time.localtime(m["ts"]))
            if day != last_day:
                last_day = day
                self._add_day(day)
            self._add_bubble(m)
        self.after(30, lambda: self.canvas.yview_moveto(1.0))

    def _add_day(self, day: str) -> None:
        wrap = tk.Frame(self.msg_frame, bg=BG)
        wrap.pack(fill="x", pady=(12, 4))
        tk.Label(wrap, text=day, bg=BG, fg=FG_TIME, font=FONT_SM).pack()
        self._bubbles.append(wrap)

    def _bubble(self, holder: tk.Frame, text: str, *, out: bool) -> tk.Canvas:
        """圆角气泡。

        用 Canvas 画平滑多边形做底，再把文字放上去：
        tkinter 的 Label 只能矩形背景，圆角必须自己画。
        """
        font = _measure_font(FONT_MSG)
        max_w = 430
        lines = _wrap_text(text, font, max_w)

        text_w = max((font.measure(l) for l in lines), default=0)
        line_h = font.metrics("linespace")
        pad_x, pad_y = 14, 10
        w = min(max_w, text_w) + pad_x * 2
        h = line_h * len(lines) + pad_y * 2

        fill = BUBBLE_OUT if out else BUBBLE_IN
        fg = BUBBLE_OUT_FG if out else BUBBLE_IN_FG

        cv = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0,
                       width=w, height=h)
        _round_rect(cv, 0, 0, w, h, 14, fill=fill, outline="")
        # 多行文字一次性绘制，保持一致行距
        cv.create_text(pad_x, pad_y, text="\n".join(lines), anchor="nw",
                       fill=fg, font=FONT_MSG, width=max_w)
        return cv

    def _thumb_card(self, holder: tk.Frame, m: dict, *, out: bool):
        """图片消息：画一张圆角缩略图卡片，点击看原图。

        Tk 的 PhotoImage 必须由 Python 侧持有引用，否则会被 GC 回收变成空白，
        所以这里把图片登记到 self._images 里一起管理生命周期。
        """
        path = m["file_path"]

        try:
            mtime = os.path.getmtime(path)
        except Exception:
            mtime = 0
        # 底色用页面底色而不是气泡色：图片自带的透明边（比如 PNG 圆角头像）
        # 若按气泡色填充，会显出一圈很突兀的彩色方框。
        key = (path, BG, mtime)
        cached = self._thumb_cache.get(key)
        if cached is not None:
            photo, w, h = cached
        else:
            data = imaging.thumbnail_png(path, 252, 252, radius=12, bg=BG,
                                         border=LINE)
            if not data:
                return None
            raw, w, h = data
            try:
                photo = tk.PhotoImage(data=raw)
            except Exception:
                return None
            self._thumb_cache[key] = (photo, w, h)
            self._images.append(photo)

        card = tk.Frame(holder, bg=BG)
        cv = tk.Canvas(card, bg=BG, highlightthickness=0, bd=0, width=w, height=h,
                       cursor="hand2")
        cv.create_image(0, 0, image=photo, anchor="nw")
        cv.pack()

        caption = m["body"] or os.path.basename(path)
        if m.get("file_size"):
            caption += f"  ·  {_fmt_size(m['file_size'])}"
        if len(caption) > 30:
            caption = caption[:29] + "…"
        lb = tk.Label(card, text=caption, bg=BG, fg=FG_TIME,
                      font=("Microsoft YaHei UI", 8), cursor="hand2")
        lb.pack(anchor="e" if out else "w", pady=(3, 0), padx=1)

        for wdg in (cv, lb):
            wdg.bind("<Button-1>", lambda e, p=path: self._open_path(p))
        return card

    def _add_bubble(self, m: dict) -> None:
        out = m["direction"] == "out"
        row = tk.Frame(self.msg_frame, bg=BG)
        row.pack(fill="x", pady=4, padx=16)

        holder = tk.Frame(row, bg=BG)
        holder.pack(side="right" if out else "left", anchor="e" if out else "w")

        # 图片消息直接渲染缩略图，不用文字气泡
        card = None
        if (m["kind"] == "file" and m.get("file_path")
                and os.path.exists(m["file_path"])
                and imaging.looks_like_image(m["file_path"])):
            card = self._thumb_card(holder, m, out=out)
            if card is not None:
                card.pack(anchor="e" if out else "w")

        if card is None:
            if m["kind"] == "file":
                body = "\U0001F4CE  " + str(m["body"])
                if m["file_size"]:
                    body += "\n" + _fmt_size(m["file_size"])
            else:
                body = m["body"]

            cv = self._bubble(holder, body, out=out)
            cv.pack(anchor="e" if out else "w")

            if m["kind"] == "file" and m["file_path"] and os.path.exists(m["file_path"]):
                cv.configure(cursor="hand2")
                cv.bind("<Button-1>", lambda e, p=m["file_path"]: self._open_path(p))

        # 发送态：只在「我发出的」消息上显示，收到的不需要
        status = (m.get("status") or "") if out else ""
        meta_text = _fmt_time(m["ts"])
        meta_color = FG_TIME
        if status == "sending":
            meta_text += "  ·  发送中…"
            meta_color = WARN_ORANGE
        elif status == "failed":
            meta_text += "  ·  ⚠ 未送达，点击重发"
            meta_color = DANGER

        meta = tk.Label(holder, text=meta_text, bg=BG, fg=meta_color, font=("Consolas", 8))
        meta.pack(anchor="e" if out else "w", pady=(3, 0), padx=2)

        if status == "failed":
            meta.configure(cursor="hand2")
            meta.bind("<Button-1>", lambda e, t=m["body"], mids=m["id"]: self._retry_text(t, mids))

        self._bubbles.append(row)

    def _on_wheel(self, evt) -> None:
        try:
            delta = -1 if evt.delta > 0 else 1
            self.canvas.yview_scroll(delta * 3, "units")
        except Exception:
            pass

    # ---------------- 发送 ----------------
    def _on_return(self, evt):
        if evt.state & 0x0001:   # Shift
            return None
        self._send_text()
        return "break"

    def _send_text(self) -> None:
        text = self.text.get("1.0", "end").strip()
        if not text:
            return
        target = self._current_peer()
        if target is None:
            messagebox.showinfo("未选择设备", "请先在左侧选择一台在线设备。", parent=self)
            return

        self.text.delete("1.0", "end")
        self.lbl_status.configure(text="发送中…", fg=WARN_ORANGE)
        self.update_idletasks()

        fp = self.current_fp
        # 先本地落库并回显：发送过程/失败都要在聊天里留下痕迹，
        # 否则用户会以为「消息根本没发出去」，也无从点重试。
        msg = self.store.add_message(
            "out", fp, target.get("alias") or "", text, kind="text", status="sending",
        )
        self._render_history()
        self._render_sidebar()

        def worker():
            # 任何异常都必须落到 failed，否则气泡会永久停在「发送中」
            try:
                res = protocol.send_text(target, self._my_info(), text, self.cfg.alias)
                ok = bool(res.ok)
                detail = res.message or ""
                if not ok:
                    # 给出可执行的下一步，而不是只丢一个「发送失败」
                    detail = detail or "发送失败"
                    if res.is_pin_required:
                        detail += "（可在设置里填写 PIN）"
                    elif res.is_busy:
                        detail += "（对方正在接收其他设备的传输，稍后重试）"
                    elif res.is_declined:
                        detail += "（对方拒绝了本次接收）"
            except Exception as e:
                ok, detail = False, f"发送异常：{e}"

            try:
                self.store.set_status(msg["id"], "sent" if ok else "failed")
            except Exception as e:
                protocol.trace("ERR", f"set_status 失败：{e}")
            try:
                self.bus.emit("msg_status", mid=msg["id"],
                              status="sent" if ok else "failed",
                              fingerprint=fp, detail=detail)
                if not ok:
                    self.bus.emit("error", text=detail)
            except Exception:
                pass

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _pick_files(self) -> None:
        target = self._current_peer()
        if target is None:
            messagebox.showinfo("未选择设备", "请先在左侧选择一台在线设备。", parent=self)
            return
        paths = filedialog.askopenfilenames(parent=self, title="选择要发送的文件")
        if not paths:
            return

        self.lbl_status.configure(text=f"发送 {len(paths)} 个文件…", fg=WARN_ORANGE)

        def progress(name: str, res) -> None:
            self.bus.emit("progress", text=f"{name} {'完成' if res.ok else '失败'}")

        def worker():
            results = protocol.send_files(
                target, self._my_info(), list(paths), progress=progress
            )
            for name, res in results:
                if res.ok:
                    p = Path(name)
                    path = ""
                    for cand in paths:
                        if os.path.basename(cand) == name:
                            path = cand
                            break
                    size = 0
                    try:
                        size = os.path.getsize(path) if path else 0
                    except Exception:
                        pass
                    msg = self.store.add_message(
                        "out", self.current_fp, target.get("alias") or "",
                        name, kind="file", file_path=path, file_size=size,
                    )
                    self.bus.emit("message", message=msg, alias="我",
                                  fingerprint=self.current_fp)
                else:
                    self.bus.emit("error", text=f"{name}：{res.message}")

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _current_peer(self) -> dict | None:
        if not self.current_fp:
            return None
        info = self.online.get(self.current_fp)
        if info is None:
            return None
        if not info.get("ip"):
            return None
        return info

    # ---------------- 后台事件 ----------------
    def _on_peer(self, info: dict) -> None:
        self.discoverer.alias = self.cfg.alias
        self.bus.emit("peer", peer=info)

    def _tick(self) -> None:
        for kind, payload in self.bus.drain():
            try:
                if kind == "peer":
                    self._handle_peer(payload)
                elif kind == "message":
                    self._handle_message(payload)
                elif kind == "incoming":
                    self._handle_incoming(payload)
                elif kind == "progress":
                    self.lbl_status.configure(text=payload.get("text", ""), fg=FG_DIM)
                elif kind == "file":
                    self.lbl_status.configure(
                        text=f"已保存 {Path(payload.get('path', '')).name}", fg=OK_GREEN
                    )
                elif kind == "scan_done":
                    self._handle_scan_done(payload)
                elif kind == "manual_probe":
                    self._handle_manual_probe(payload)
                elif kind == "msg_status":
                    self._handle_msg_status(payload)
                elif kind == "error":
                    self.lbl_status.configure(text=payload.get("text", ""), fg="#e05c5c")
            except Exception:
                pass
        self.after(180, self._tick)

    def _handle_manual_probe(self, payload: dict) -> None:
        """手动「添加设备」结果：明确输入 IP 就不走任何过滤。"""
        self._manual_result = (payload.get("ip"), payload.get("info"))
        peer = payload.get("info")
        if not peer:
            return
        fp = (peer.get("fingerprint") or "").strip()
        if not fp:
            return
        # 即使这台设备曾被屏蔽，手动输入 IP 也必须能建立会话
        self._blocked = force_visible(fp, self._blocked)
        try:
            self.store.unblock_peer(fp)
        except Exception:
            pass
        self.online[fp] = peer
        self.store.upsert_peer(peer, peer.get("ip") or "")
        self._reload_keep_selection()
        self.lbl_status.configure(
            text=f"已添加 {peer.get('alias')} @ {peer.get('ip')}", fg=OK_GREEN)

    def _handle_msg_status(self, payload: dict) -> None:
        """发送结果回填：送达成「已发送」，失败标红并可点重发。"""
        fp = payload.get("fingerprint") or ""
        status = payload.get("status") or ""
        detail = payload.get("detail") or ""
        # 落库必须放在「是否当前会话」判断之前：
        # 群发时其他设备的回执同样要写进去，否则那些会话永远停在「发送中」。
        mid = payload.get("mid")
        if mid and status in ("sending", "sent", "failed"):
            try:
                self.store.set_status(mid, status)
            except Exception:
                pass
        if fp and fp != self.current_fp:
            self._render_sidebar()
            return
        if status == "sent":
            self.lbl_status.configure(text="已送达", fg=OK_GREEN)
        elif status == "failed":
            self.lbl_status.configure(
                text=detail or "未送达", fg="#e05c5c")
        self._render_history()
        self._render_sidebar()

    def _retry_text(self, body: str, mid: str) -> None:
        """点击「未送达」重新发送同一条消息。"""
        target = self._current_peer()
        if target is None:
            messagebox.showinfo("设备不在线", "请先让对方上线，或在左侧重新选择设备。",
                                parent=self)
            return
        self.lbl_status.configure(text="重发中…", fg=WARN_ORANGE)
        self.store.set_status(mid, "sending")
        self._render_history()

        fp = self.current_fp

        def worker():
            res = protocol.send_text(target, self._my_info(), body, self.cfg.alias)
            if res.ok:
                self.store.set_status(mid, "sent")
                self.bus.emit("msg_status", mid=mid, status="sent", fingerprint=fp)
            else:
                d = res.message or "发送失败"
                self.store.set_status(mid, "failed")
                self.bus.emit("msg_status", mid=mid, status="failed",
                              fingerprint=fp, detail=d)
                self.bus.emit("error", text=d)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _handle_peer(self, payload: dict) -> None:
        info = payload.get("peer") or {}
        fp = (info.get("fingerprint") or "").strip()
        if not fp:
            return
        # 只有「屏蔽」会过滤发现结果；「删除」不写名单，刷新即可重新出现
        if fp in self._blocked:
            return
        self.online[fp] = info
        self.store.upsert_peer(info, info.get("ip") or "")
        self._reload_keep_selection()

    def _handle_message(self, payload: dict) -> None:
        msg = payload.get("message") or {}
        fp = msg.get("peer_fp") or payload.get("fingerprint") or ""
        if fp == self.current_fp:
            self._add_bubble(msg)
            self.after(30, lambda: self.canvas.yview_moveto(1.0))
        if msg.get("direction") == "in":
            self.lbl_status.configure(text=f"新消息来自 {payload.get('alias', '')}", fg=ACCENT)
        else:
            self.lbl_status.configure(text="已发送", fg=OK_GREEN)
        self._reload_keep_selection()

    def _handle_incoming(self, payload: dict) -> None:
        alias = payload.get("alias") or "未知设备"
        files = payload.get("files") or []
        if len(files) == 1 and str(files[0].get("fileType", "")).startswith("text/plain"):
            self.lbl_status.configure(text=f"{alias} 正在发送消息…", fg=WARN_ORANGE)
        else:
            self.lbl_status.configure(
                text=f"{alias} 正在发送 {len(files)} 个文件…", fg=WARN_ORANGE
            )

    def _handle_scan_done(self, payload: dict) -> None:
        found = int(payload.get("found") or 0)
        st = payload.get("stats") or {}
        # 计数一律以过滤后的可见列表为准，保证顶栏数字 = 左侧数得出来的数量
        self._load_conversations()
        online = count_online([r for _k, r in self._rows])
        if online:
            self.lbl_status.configure(
                text=f"扫描完成：在线 {online} 台（本次新发现 {found} 台）", fg=OK_GREEN)
        else:
            # 零设备时要说清「发了没人回」还是「根本没发出去」，否则用户无从下手
            sent = int(st.get("sent") or 0)
            recv = int(st.get("recv") or 0)
            joined = int(st.get("joined") or 0)
            port = int(st.get("bound_port") or 0)
            hint = "请确认与手机在同一个 Wi-Fi"
            if joined == 0:
                hint = "组播加入失败，请点「诊断」查看"
            elif sent and recv == 0:
                hint = "宣告已发出但无回应，多为防火墙/AP 隔离，请点「诊断」"
            self.lbl_status.configure(
                text=f"未发现设备（端口 {port}，发包 {sent}，收包 {recv}）· {hint}",
                fg=WARN_ORANGE)

    def _reload_keep_selection(self) -> None:
        """刷新侧边栏数据，保持当前会话高亮，不重绘右侧历史。"""
        self._load_conversations()

    # ---------------- 动作 ----------------
    def _refresh(self) -> None:
        """刷新 = 子网主动扫描。

        重启 UDp 监听没有意义（端口没变、规则没变），真正能改变结果的是
        主动向 1-254 逐个建立 TCP 连接拉 /info —— 这条路径走本机出站，
        不受「入站被防火墙拦截」影响，是零设备场景下唯一可靠的自救。
        """
        if getattr(self, "_scanning_ui", False):
            return
        self._scanning_ui = True
        self.lbl_status.configure(text="正在扫描局域网（组播 + 子网）…", fg=WARN_ORANGE)

        def worker():
            found = 0
            try:
                found = self.discoverer.scan_subnet()
            except Exception as e:
                self.bus.emit("error", text=f"扫描出错：{e}")
            st = self.discoverer.stats.as_dict()
            self._scanning_ui = False
            visible = count_online(
                [r for _k, r in getattr(self, "_rows", [])])
            self.bus.emit("scan_done", found=found, online=visible, stats=st)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _open_share(self) -> None:
        """打开「链接分享」窗口（单例，重复点只抬到前面）。"""
        try:
            if self._share_dialog is not None and self._share_dialog.winfo_exists():
                self._share_dialog.lift()
                self._share_dialog.focus_force()
                return
        except Exception:
            pass
        self._share_dialog = ShareDialog(self)

    def _open_add_peer(self) -> None:
        AddPeerDialog(self)

    def _open_diag(self) -> None:
        DiagDialog(self)

    def _open_downloads(self) -> None:
        self._open_path(str(self.cfg.download_dir))

    def _open_path(self, path: str) -> None:
        try:
            if os.name == "nt":
                os.startfile(path)   # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("打开失败", f"{path}\n{e}", parent=self)

    def _open_settings(self) -> None:
        SettingsDialog(self)

    def _open_theme(self) -> None:
        ThemeDialog(self)

    def _open_group(self) -> None:
        GroupDialog(self)

    def group_send(self, targets: list[dict], text: str = "",
                   files: list[str] | None = None, on_progress=None) -> int:
        """一对多群发：同一段文字 / 同一批文件，逐台设备发出去。

        每条消息先按设备落库（status=sending），发完再回填 sent/failed，
        这样任何一台失败都能在它自己的会话里点着重发，而不是整体失败无从下手。
        """
        import threading

        files = list(files or [])
        jobs: list[tuple[dict, dict, str, str]] = []

        for t in targets:
            fp = t.get("fingerprint") or ""
            alias = t.get("alias") or ""
            if text:
                msg = self.store.add_message(
                    "out", fp, alias, text, kind="text", status="sending")
                jobs.append((t, msg, "text", ""))
            for fpath in files:
                size = 0
                try:
                    size = os.path.getsize(fpath)
                except Exception:
                    pass
                msg = self.store.add_message(
                    "out", fp, alias, os.path.basename(fpath), kind="file",
                    file_path=fpath, file_size=size, status="sending")
                jobs.append((t, msg, "file", fpath))

        if not jobs:
            return 0

        self._render_history()
        self._render_sidebar()

        total = len(jobs)

        def worker():
            ok_n = 0
            done = 0
            for t, msg, kind, fpath in jobs:
                fp = t.get("fingerprint") or ""
                alias = t.get("alias") or ""
                ok, detail = False, "未执行"
                try:
                    if kind == "text":
                        res = protocol.send_text(
                            t, self._my_info(), text, self.cfg.alias)
                        ok, detail = res.ok, (res.message or "")
                        if not ok and res.is_pin_required:
                            detail += "（需要 PIN）"
                        elif not ok and res.is_declined:
                            detail += "（对方拒收）"
                    else:
                        results = protocol.send_files(t, self._my_info(), [fpath])
                        ok = bool(results) and all(r.ok for _nm, r in results)
                        detail = results[0][1].message if results else "发送失败"
                except Exception as e:
                    ok, detail = False, f"异常：{e}"

                # 状态回填必须自己吞掉异常：写库失败也不能中断后面设备的发送，
                # 否则剩余消息会永久停在「发送中」。
                try:
                    self.store.set_status(msg["id"], "sent" if ok else "failed")
                except Exception as e:
                    protocol.trace("ERR", f"set_status 失败 {msg['id']}: {e}")
                try:
                    self.bus.emit(
                        "msg_status", mid=msg["id"],
                        status="sent" if ok else "failed", fingerprint=fp,
                        detail=detail or "")
                except Exception:
                    pass

                done += 1
                if ok:
                    ok_n += 1
                if on_progress:
                    try:
                        on_progress(done, total, ok_n, alias)
                    except Exception:
                        pass

            self.bus.emit("progress", text=f"群发完成：成功 {ok_n}/{total} 条")

        threading.Thread(target=worker, daemon=True).start()
        return total

    def _switch_theme(self, name: str) -> None:
        """切换主题并立刻重建界面。

        配色是在建控件时读全局槽位的，所以换主题必须重建。
        重建只重做视图，不动服务端、发现器和数据库连接，
        因此正在进行的传输和消息记录都不受影响。
        """
        if name == THEME_NAME:
            return
        self.cfg.set("theme", name)
        apply_theme(name)

        for w in self.winfo_children():
            w.destroy()
        self._bubbles = []
        self._row_widgets = {}
        # 缩略图/图标的底色是按旧主题烘焙进 PNG 的，必须丢弃重做，
        # 否则换肤后图片周围会留一圈旧底色的方块。
        self._thumb_cache.clear()
        self._images.clear()
        for attr in [a for a in vars(self) if a.startswith("_logo_")]:
            delattr(self, attr)
        self._build()

        # 重建后把原来的会话与消息重新铺上，视觉上是「原地换肤」
        self._render_sidebar()
        self._render_history()
        self._restore_header()
        self.lbl_status.configure(text="●  已切换主题：" + _theme.label(name), fg=OK_GREEN)

    def _restore_header(self) -> None:
        """重建界面后恢复右侧标题区。

        _build() 会把标题重置成初始文案，而 current_fp 还在，
        不补这一步就会出现「消息都在、标题却是空白提示」的割裂感。
        """
        fp = getattr(self, "current_fp", None)
        if not fp:
            return
        row = next((r for _k, r in self._rows if r["fp"] == fp), None)
        if row is None:
            return
        try:
            self.lbl_title.configure(text=row["alias"])
            # 行数据把设备信息放在 info 里，这里要与 _open_row 保持同一取数口径，
            # 否则换肤后副标题会少掉型号和 IP。
            info = row.get("info") or {}
            state = "在线" if row["online"] else "离线（仅历史）"
            bits = [state]
            if info.get("deviceModel"):
                bits.append(info["deviceModel"])
            if info.get("ip"):
                bits.append(info["ip"])
            self.lbl_sub.configure(text=" · ".join(bits))
            ch = (row["alias"] or "?").strip()[:1].upper() or "?"
            self.head_avatar.configure(
                text=ch,
                bg=ACCENT if row["online"] else BG_HOVER,
                fg="white" if row["online"] else FG_TIME)
        except Exception:
            pass

    # ---------------- 关闭 ----------------
    def _on_close(self) -> None:
        try:
            self.discoverer.stop()
        except Exception:
            pass
        try:
            self.share.stop()
        except Exception:
            pass
        try:
            self.server.stop()
        except Exception:
            pass
        try:
            self.store.close()
        except Exception:
            pass
        self.destroy()


class ShareDialog(tk.Toplevel):
    """链接分享窗口。

    UI 只负责两件事：把文件塞进 ShareServer，以及把地址呈现出来让人扫/复制。
    服务本身跑在独立线程的 HTTP 端口上，所以关掉这个窗口分享依然在，
    直到用户点「停止分享」或退出程序。
    """

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.share = app.share
        self.title("链接分享")
        self.configure(bg=BG)
        self.geometry("620x600")
        self.minsize(560, 520)
        self.transient(app)
        self._items_frame: tk.Frame | None = None
        self._build()
        self._sync()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- 布局 ----------------
    def _build(self) -> None:
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(head, text="链接分享", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        self.lbl_state = tk.Label(head, text="未开启", bg=BG, fg=FG_TIME, font=FONT_SM)
        self.lbl_state.pack(side="right")

        tk.Label(self, text="电脑开着这个程序时，同一 Wi-Fi 下的手机/平板用浏览器打开下面地址即可下载。",
                 bg=BG, fg=FG_DIM, font=FONT_SM, anchor="w", justify="left",
                 wraplength=570).pack(fill="x", padx=18)

        # --- 地址卡 ---
        card = tk.Frame(self, bg=BG_INPUT)
        card.pack(fill="x", padx=18, pady=(10, 8))
        inner = tk.Frame(card, bg=BG_INPUT)
        inner.pack(fill="x", padx=12, pady=10)
        self.lbl_url = tk.Label(inner, text="未开启分享", bg=BG_INPUT, fg=ACCENT,
                                font=("Consolas", 11), anchor="w")
        self.lbl_url.pack(side="left", fill="x", expand=True)

        brow = tk.Frame(self, bg=BG)
        brow.pack(fill="x", padx=18, pady=(0, 10))
        self.btn_toggle = self.app._chip(brow, "开启分享", self._toggle, kind="primary",
                                         padx=16)
        self.btn_toggle.pack(side="left")
        self.app._chip(brow, "复制链接", self._copy_url, kind="solid").pack(side="left", padx=6)
        self.app._chip(brow, "＋ 添加文件", self._add_files, kind="solid").pack(side="left", padx=6)
        self.app._chip(brow, "粘贴文字", self._add_text, kind="solid").pack(side="left", padx=6)

        tk.Frame(self, bg=LINE, height=1).pack(fill="x", padx=18, pady=(2, 8))

        # --- 内容列表 ---
        tk.Label(self, text="分享内容", bg=BG, fg=FG_DIM, font=FONT_SM,
                 anchor="w").pack(fill="x", padx=18)
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=(6, 8))
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.items_frame = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.items_frame, anchor="nw")
        self.items_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_wheel, add="+")

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=18, pady=(0, 14))
        tk.Label(foot, text="提示：手机打不开时，确认手机与电脑在同一 Wi-Fi，且未被路由器「AP 隔离」挡住。",
                 bg=BG, fg=FG_TIME, font=("Microsoft YaHei UI", 8), anchor="w",
                 justify="left", wraplength=430).pack(side="left", fill="x", expand=True)
        self.app._chip(foot, "关闭窗口", self._on_close, kind="solid").pack(side="right")

    def _on_wheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    # ---------------- 状态 ----------------
    def _toggle(self) -> None:
        if self.share.running:
            self.share.stop()
        else:
            if not self.share.start():
                messagebox.showerror(
                    "无法开启",
                    f"分享端口被占用或网络受限：{self.share.last_error or '未知原因'}\n"
                    "请检查防火墙是否拦截了本程序。", parent=self)
                return
        self._sync()

    def _sync(self) -> None:
        running = self.share.running
        try:
            self.btn_toggle.configure(
                text="停止分享" if running else "开启分享",
                bg=LINE if running else ACCENT,
                fg=FG if running else "white")
        except Exception:
            pass
        if running:
            url = self.share.url()
            self.lbl_url.configure(text=url)
            self.lbl_state.configure(text=f"● 分享中 · 端口 {self.share.port}", fg=OK_GREEN)
        else:
            self.lbl_url.configure(text="未开启分享")
            self.lbl_state.configure(text="未开启", fg=FG_TIME)
        self._render_items()

    def _copy_url(self) -> None:
        if not self.share.running:
            messagebox.showinfo("还没开启", "请先点「开启分享」。", parent=self)
            return
        self.app._copy(self.share.url())

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(parent=self, title="选择要分享的文件")
        if not paths:
            return
        n = self.share.add_paths(paths)
        if n == 0:
            messagebox.showinfo("没有新增", "这些文件已在分享列表里了。", parent=self)
        elif not self.share.running:
            self.share.start()
        self._sync()

    def _add_text(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("粘贴文字")
        dlg.configure(bg=BG)
        dlg.geometry("480x300")
        dlg.transient(self)
        tk.Label(dlg, text="要分享的文字（手机端可一键复制）", bg=BG, fg=FG_DIM,
                 font=FONT_SM).pack(anchor="w", padx=14, pady=(12, 6))
        box = tk.Frame(dlg, bg=LINE)
        box.pack(fill="both", expand=True, padx=14)
        txt = tk.Text(box, bg=BG_INPUT, fg=FG, font=FONT_UI, relief="flat", bd=0,
                      wrap="word", padx=10, pady=9, insertbackground=FG)
        txt.pack(fill="both", expand=True, padx=1, pady=1)
        txt.focus_set()

        def ok():
            val = txt.get("1.0", "end").rstrip("\n")
            self.share.text = val
            if val and not self.share.running:
                self.share.start()
            dlg.destroy()
            self._sync()

        row = tk.Frame(dlg, bg=BG)
        row.pack(fill="x", padx=14, pady=12)
        self.app._chip(row, "确定", ok, kind="primary", padx=18).pack(side="right")
        self.app._chip(row, "取消", dlg.destroy, kind="solid").pack(side="right", padx=6)

    def _render_items(self) -> None:
        for w in self.items_frame.winfo_children():
            w.destroy()

        items = list(self.share.items)
        text = (self.share.text or "").strip()

        if text:
            card = tk.Frame(self.items_frame, bg=BG_INPUT)
            card.pack(fill="x", pady=3)
            top = tk.Frame(card, bg=BG_INPUT)
            top.pack(fill="x", padx=10, pady=(7, 0))
            tk.Label(top, text="📝  文字消息", bg=BG_INPUT, fg=FG,
                     font=FONT_SM).pack(side="left")
            self.app._chip(top, "移除", lambda: self._drop_text(), kind="solid",
                           padx=8, pady=2).pack(side="right")
            preview = text[:80].replace("\n", " ")
            tk.Label(card, text=preview + ("…" if len(text) > 80 else ""),
                     bg=BG_INPUT, fg=FG_TIME, font=FONT_SM, anchor="w",
                     justify="left", wraplength=520).pack(fill="x", padx=10, pady=(2, 7))

        if not items and not text:
            tip = tk.Label(self.items_frame, text="还没有内容\n点上方「添加文件」或「粘贴文字」",
                           bg=BG, fg=FG_TIME, font=FONT_SM, justify="center")
            tip.pack(pady=40)
            return

        for it in items:
            self._make_item(it)

    def _make_item(self, it) -> None:
        card = tk.Frame(self.items_frame, bg=BG_INPUT)
        card.pack(fill="x", pady=3)
        inner = tk.Frame(card, bg=BG_INPUT)
        inner.pack(fill="x", padx=10, pady=7)

        tk.Label(inner, text="🖼" if it.is_image else "📦", bg=BG_INPUT, fg=FG,
                 font=("Microsoft YaHei UI", 13)).pack(side="left", padx=(0, 8))
        col = tk.Frame(inner, bg=BG_INPUT)
        col.pack(side="left", fill="x", expand=True)
        tk.Label(col, text=it.name, bg=BG_INPUT, fg=FG, font=FONT_SM, anchor="w"
                 ).pack(fill="x")
        tk.Label(col, text=share._fmt_size(it.size), bg=BG_INPUT, fg=FG_TIME,
                 font=("Microsoft YaHei UI", 8), anchor="w").pack(fill="x")

        self.app._chip(inner, "移除", lambda s=it.sid: self._drop(s), kind="solid",
                       padx=9, pady=2).pack(side="right")

    def _drop(self, sid: str) -> None:
        self.share.remove(sid)
        self._render_items()

    def _drop_text(self) -> None:
        self.share.text = ""
        self._render_items()

    def _on_close(self) -> None:
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


class ThemeDialog(tk.Toplevel):
    """配色选择器：每个主题一张带真实配色的小卡片。

    预览卡片直接用该主题自己的色值绘制（不依赖当前主题），
    所以即使现在是深色，也能准确看到浅色主题长什么样。
    """

    CARD_W, CARD_H = 168, 104

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.title("选择配色")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()
        self._build()

    def _build(self) -> None:
        pad = 22
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=pad, pady=(18, 0))

        tk.Label(wrap, text="选择配色", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 13, "bold"), anchor="w").pack(fill="x")
        tk.Label(wrap, text="点一下立即生效，界面会原地换肤，聊天记录不受影响。",
                 bg=BG, fg=FG_DIM, font=FONT_SM, anchor="w").pack(fill="x", pady=(3, 14))

        for title, keys in (("深色", [k for k in _theme.ORDER if not _theme.is_light(k)]),
                            ("浅色", [k for k in _theme.ORDER if _theme.is_light(k)])):
            if not keys:
                continue
            tk.Label(wrap, text=title, bg=BG, fg=FG_TIME, font=FONT_SM,
                     anchor="w").pack(fill="x", pady=(4, 6))
            row = tk.Frame(wrap, bg=BG)
            row.pack(fill="x", pady=(0, 10))
            for k in keys:
                self._card(row, k).pack(side="left", padx=(0, 10))

        tk.Frame(self, bg=BG).pack(fill="x", pady=(0, 4))
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=pad, pady=(0, 18))
        cur = _theme.label(THEME_NAME)
        tk.Label(foot, text=f"当前：{cur}", bg=BG, fg=FG_DIM,
                 font=FONT_SM).pack(side="left")
        self._chip(foot, "关闭", self.destroy, kind="solid",
                   padx=18, pady=6).pack(side="right")

    def _chip(self, parent, text, cmd, *, kind="ghost", padx=13, pady=6):
        if kind == "primary":
            bg, fg, hov = ACCENT, "white", ACCENT_DARK
        elif kind == "solid":
            bg, fg, hov = BG_HOVER, FG, BG_SEL
        else:
            bg, fg, hov = BG_SIDE, FG_DIM, BG_HOVER
        lb = tk.Label(parent, text=text, bg=bg, fg=fg, font=FONT_SM,
                      padx=padx, pady=pady, cursor="hand2")
        lb.bind("<Button-1>", lambda e: cmd())
        lb.bind("<Enter>", lambda e: lb.configure(bg=hov))
        lb.bind("<Leave>", lambda e: lb.configure(bg=bg))
        return lb

    def _card(self, parent: tk.Frame, key: str) -> tk.Frame:
        t = _theme.THEMES[key]
        active = key == THEME_NAME
        w, h = self.CARD_W, self.CARD_H

        box = tk.Frame(parent, bg=BG, highlightthickness=2,
                       highlightbackground=ACCENT if active else LINE,
                       cursor="hand2")
        cv = tk.Canvas(box, width=w, height=h, bg=t["bg"], highlightthickness=0, bd=0)
        cv.pack()

        # 预览：左侧栏 + 两条气泡，用主题自身色值画出来
        cv.create_rectangle(0, 0, 40, h, fill=t["side"], outline="")
        cv.create_oval(9, 12, 21, 24, fill=t["accent"], outline="")
        cv.create_rectangle(9, 32, 31, 36, fill=t["dim"], outline="")
        cv.create_rectangle(9, 42, 27, 46, fill=t["line"], outline="")

        # 收到的气泡（左）
        cv.create_rectangle(50, 16, 124, 40, fill=t["in_bg"], outline="")
        # 发出的气泡（右）
        cv.create_rectangle(78, 50, 158, 74, fill=t["out_bg"], outline="")
        # 输入框示意
        cv.create_rectangle(50, 84, 158, 96, fill=t["input"], outline=t["line"])
        # 强调色按钮
        cv.create_rectangle(134, 84, 158, 96, fill=t["accent"], outline="")

        # 名称与说明用「当前主题」的颜色，保证在任何主题下都看得清
        cap = tk.Frame(box, bg=BG)
        cap.pack(fill="x", padx=10, pady=(7, 9))
        name_row = tk.Frame(cap, bg=BG)
        name_row.pack(fill="x")
        tk.Label(name_row, text=t["label"], bg=BG, fg=FG if active else FG_DIM,
                 font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(side="left")
        if active:
            tk.Label(name_row, text="✓", bg=BG, fg=ACCENT,
                     font=("Microsoft YaHei UI", 10, "bold")).pack(side="right")
        tk.Label(cap, text=t["desc"], bg=BG, fg=FG_TIME, font=("Microsoft YaHei UI", 8),
                 anchor="w", justify="left", wraplength=w - 10).pack(fill="x", pady=(2, 0))

        def pick():
            self.app._switch_theme(key)
            self.destroy()

        for wdg in (box, cv, cap, name_row):
            wdg.bind("<Button-1>", lambda e: pick())
        for child in cap.winfo_children():
            child.bind("<Button-1>", lambda e: pick())

        return box


class GroupDialog(tk.Toplevel):
    """一对多群发。

    左侧勾选在线设备，右侧写内容（可附文件），一次发给所有人。
    每台设备的发送结果各自回填到它自己的会话里，失败的能单独重发。
    """

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.title("群发消息")
        self.configure(bg=BG)
        self.transient(app)
        self.resizable(False, False)

        self._files: list[str] = []
        self._checks: dict[str, tk.BooleanVar] = {}
        self._items: list[dict] = []

        self._build()
        self._center()
        self.grab_set()

    def _center(self) -> None:
        # 高度按内容自适应：固定高度在不同 DPI/字体下容易把底部按钮裁掉
        self.update_idletasks()
        w = 620
        h = max(470, self.winfo_reqheight())
        px = self.app.winfo_rootx() + (self.app.winfo_width() - w) // 2
        py = self.app.winfo_rooty() + (self.app.winfo_height() - h) // 3
        self.geometry(f"{w}x{h}+{max(0, px)}+{max(0, py)}")

    def _build(self) -> None:
        # ---------- 标题 ----------
        head = tk.Frame(self, bg=BG_SIDE, height=54)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="群发消息", bg=BG_SIDE, fg=FG,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=18)
        self.lbl_n = tk.Label(head, text="", bg=BG_SIDE, fg=FG_DIM, font=FONT_SM)
        self.lbl_n.pack(side="right", padx=18)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        # ---------- 设备多选 ----------
        top = tk.Frame(body, bg=BG)
        top.pack(fill="x")

        bar = tk.Frame(top, bg=BG)
        bar.pack(fill="x")
        tk.Label(bar, text="选择接收设备", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        self._chip(bar, "全不选", lambda: self._toggle_all(False)).pack(side="right", padx=3)
        self._chip(bar, "全选", lambda: self._toggle_all(True)).pack(side="right", padx=3)

        list_wrap = tk.Frame(top, bg=LINE)
        list_wrap.pack(fill="x", pady=(6, 0))

        inner = tk.Frame(list_wrap, bg=BG_INPUT)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self.dl_canvas = tk.Canvas(inner, bg=BG_INPUT, highlightthickness=0,
                                   bd=0, height=150)
        sb = tk.Scrollbar(inner, orient="vertical", command=self.dl_canvas.yview,
                          width=8, bg=BG_INPUT, troughcolor=BG_INPUT, bd=0,
                          relief="flat", activebackground=LINE)
        self.dl_inner = tk.Frame(self.dl_canvas, bg=BG_INPUT)
        self._dl_win = self.dl_canvas.create_window(
            (0, 0), window=self.dl_inner, anchor="nw")
        self.dl_canvas.configure(yscrollcommand=sb.set)
        self.dl_canvas.pack(side="left", fill="both", expand=True, padx=(2, 0), pady=2)
        sb.pack(side="right", fill="y", pady=2)
        self.dl_inner.bind(
            "<Configure>",
            lambda e: self.dl_canvas.configure(
                scrollregion=self.dl_canvas.bbox("all")))
        self.dl_canvas.bind(
            "<Configure>",
            lambda e: self.dl_canvas.itemconfigure(self._dl_win, width=e.width))

        # ---------- 内容 ----------
        tk.Label(body, text="消息内容", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(12, 4))

        box = tk.Frame(body, bg=LINE)
        box.pack(fill="both", expand=True)
        tb = tk.Frame(box, bg=BG_INPUT)
        tb.pack(fill="both", expand=True, padx=1, pady=1)
        self.txt = tk.Text(tb, height=5, bg=BG_INPUT, fg=FG, font=FONT_MSG,
                           relief="flat", bd=0, highlightthickness=0,
                           insertbackground=FG, wrap="word", padx=10, pady=8)
        self.txt.pack(fill="both", expand=True)
        self.txt.bind("<FocusIn>", lambda e: box.configure(bg=ACCENT))
        self.txt.bind("<FocusOut>", lambda e: box.configure(bg=LINE))

        # ---------- 附件 ----------
        frow = tk.Frame(body, bg=BG)
        frow.pack(fill="x", pady=(10, 0))
        self._chip(frow, "＋ 添加文件", self._add_files, kind="solid").pack(side="left")
        self.lbl_files = tk.Label(frow, text="未选择文件", bg=BG, fg=FG_TIME,
                                  font=FONT_SM, anchor="w")
        self.lbl_files.pack(side="left", padx=10)

        # ---------- 底部 ----------
        self.lbl_prog = tk.Label(self, text="", bg=BG, fg=FG_DIM, font=FONT_SM)
        self.lbl_prog.pack(fill="x", padx=18)

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=16, pady=(6, 14))
        self.btn_send = tk.Label(foot, text="发送给所选设备", bg=ACCENT, fg="white",
                                 font=("Microsoft YaHei UI", 10, "bold"),
                                 padx=20, pady=7, cursor="hand2")
        self.btn_send.pack(side="right")
        self.btn_send.bind("<Button-1>", lambda e: self._send())
        self.btn_send.bind("<Enter>", lambda e: self.btn_send.configure(bg=ACCENT_DARK))
        self.btn_send.bind("<Leave>", lambda e: self.btn_send.configure(bg=ACCENT))

        self._chip(foot, "取消", self.destroy).pack(side="right", padx=8)

        self._load_devices()

    # ---------- 组件 ----------
    def _chip(self, parent, text, cmd, *, kind="ghost", padx=11, pady=4):
        if kind == "solid":
            bg, fg, hov = BG_HOVER, FG, BG_SEL
        else:
            bg, fg, hov = BG_SIDE, FG_DIM, BG_HOVER
        lb = tk.Label(parent, text=text, bg=bg, fg=fg, font=FONT_SM,
                      padx=padx, pady=pady, cursor="hand2")
        lb.bind("<Button-1>", lambda e: cmd())
        lb.bind("<Enter>", lambda e: lb.configure(bg=hov, fg=FG))
        lb.bind("<Leave>", lambda e: lb.configure(bg=bg, fg=fg))
        return lb

    def _load_devices(self) -> None:
        for w in self.dl_inner.winfo_children():
            w.destroy()
        self._checks.clear()
        self._items = []

        online = [info for _fp, info in self.app.online.items() if info.get("ip")]
        online.sort(key=lambda i: (i.get("alias") or ""))

        if not online:
            tk.Label(self.dl_inner, text="当前没有在线设备。\n先让对方打开 LocalSend，"
                                        "再点主界面的「刷新设备」。",
                     bg=BG_INPUT, fg=FG_TIME, font=FONT_SM,
                     justify="left").pack(anchor="w", padx=12, pady=16)
            self.lbl_n.configure(text="0 台在线")
            return

        for info in online:
            fp = info.get("fingerprint") or ""
            if not fp:
                continue
            self._items.append(info)
            var = tk.BooleanVar(value=True)
            self._checks[fp] = var

            row = tk.Frame(self.dl_inner, bg=BG_INPUT, cursor="hand2")
            row.pack(fill="x", padx=6, pady=1)

            cb = tk.Checkbutton(
                row, variable=var, bg=BG_INPUT, activebackground=BG_INPUT,
                fg=FG, selectcolor=BG_SEL, bd=0, highlightthickness=0,
                command=self._update_count)
            cb.pack(side="left", padx=(4, 0))

            ch = (info.get("alias") or "?").strip()[:1].upper() or "?"
            av = tk.Label(row, text=ch, bg=ACCENT, fg="white", width=2,
                          font=("Microsoft YaHei UI", 9, "bold"))
            av.pack(side="left", padx=(2, 8), pady=3)

            nm = tk.Label(row, text=info.get("alias") or "未知设备", bg=BG_INPUT,
                          fg=FG, font=FONT_NAME, anchor="w")
            nm.pack(side="left")

            meta = []
            if info.get("deviceModel"):
                meta.append(info["deviceModel"])
            if info.get("ip"):
                meta.append(info["ip"])
            if meta:
                tk.Label(row, text=" · ".join(meta), bg=BG_INPUT, fg=FG_TIME,
                         font=FONT_SM).pack(side="left", padx=8)

            for w in (row, av, nm):
                w.bind("<Button-1>", lambda e, v=var: (v.set(not v.get()),
                                                       self._update_count()))

        self._update_count()

    def _toggle_all(self, on: bool) -> None:
        for v in self._checks.values():
            v.set(on)
        self._update_count()

    def _update_count(self) -> None:
        n = sum(1 for v in self._checks.values() if v.get())
        self.lbl_n.configure(text=f"选中 {n} / {len(self._checks)} 台")

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(parent=self, title="选择要群发的文件")
        if paths:
            self._files.extend([p for p in paths if p not in self._files])
            names = ", ".join(os.path.basename(x) for x in self._files[:3])
            if len(self._files) > 3:
                names += f" 等 {len(self._files)} 个"
            self.lbl_files.configure(text=names, fg=FG)

    # ---------- 发送 ----------
    def _send(self) -> None:
        targets = [info for info in self._items
                   if self._checks.get(info.get("fingerprint") or "",
                                       tk.BooleanVar(value=False)).get()]
        if not targets:
            messagebox.showinfo("未选择设备", "请至少勾选一台设备。", parent=self)
            return

        text = self.txt.get("1.0", "end").strip()
        if not text and not self._files:
            messagebox.showinfo("没有内容", "请写点内容，或添加要发送的文件。",
                                parent=self)
            return

        self.btn_send.configure(bg=BG_HOVER, fg=FG_DIM)
        self.lbl_prog.configure(text="正在发送…", fg=WARN_ORANGE)
        self.update_idletasks()

        # 跨线程不能直接碰 Tk 控件：先入队，再由主线程 after() 轮询消费。
        import queue

        q: "queue.Queue[tuple]" = queue.Queue()

        def progress(done: int, total: int, ok_n: int, alias: str) -> None:
            try:
                q.put((done, total, ok_n, alias))
            except Exception:
                pass

        def drain() -> None:
            try:
                while True:
                    done, total, ok_n, alias = q.get_nowait()
                    self.lbl_prog.configure(
                        text=f"进度 {done}/{total} · 成功 {ok_n} · 最近：{alias}",
                        fg=FG_DIM)
            except Exception:
                pass
            if self.winfo_exists():
                self.after(120, drain)

        self.after(120, drain)

        n = self.app.group_send(targets, text=text, files=self._files,
                                on_progress=progress)
        self.lbl_prog.configure(
            text=f"已提交 {n} 条（{len(targets)} 台设备），结果会回填到各自会话",
            fg=OK_GREEN)
        # 不自动关窗：让用户看到真实进度，失败项也留在这里核对
        self.btn_send.configure(bg=ACCENT, fg="white")


class SettingsDialog(tk.Toplevel):
    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.title("设置")
        self.configure(bg=BG)
        self.geometry("560x420")
        self.transient(app)
        self.grab_set()
        self._build()

    def _build(self) -> None:
        cfg = self.app.cfg
        pad = {"padx": 16, "pady": 6}

        tk.Label(self, text="设备名称", bg=BG, fg=FG_DIM, font=FONT_SM).pack(
            anchor="w", **pad)
        self.e_alias = tk.Entry(self, bg=BG_INPUT, fg=FG, font=FONT_UI, relief="flat",
                                insertbackground=FG)
        self.e_alias.insert(0, cfg.alias)
        self.e_alias.pack(fill="x", padx=16, ipady=5)

        tk.Label(self, text="接收文件目录", bg=BG, fg=FG_DIM, font=FONT_SM).pack(
            anchor="w", **pad)
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=16)
        self.e_dir = tk.Entry(row, bg=BG_INPUT, fg=FG, font=FONT_UI, relief="flat",
                              insertbackground=FG)
        self.e_dir.insert(0, str(cfg.download_dir))
        self.e_dir.pack(side="left", fill="x", expand=True, ipady=5)
        tk.Button(row, text="浏览", command=self._browse, bg=BG_HOVER, fg=FG,
                  font=FONT_SM, relief="flat", bd=0, padx=12, cursor="hand2").pack(
            side="left", padx=(6, 0))

        info = certs.summary()
        tk.Label(self, text="本机指纹（与手机端比对可确认身份）", bg=BG, fg=FG_DIM,
                 font=FONT_SM).pack(anchor="w", **pad)
        fp_box = tk.Text(self, height=3, bg=BG_INPUT, fg=FG_DIM, font=("Consolas", 9),
                         relief="flat", bd=0, wrap="char", padx=8, pady=6)
        fp_box.insert("1.0", info["upper"])
        fp_box.configure(state="disabled")
        fp_box.pack(fill="x", padx=16)

        tk.Label(
            self, bg=BG, fg=FG_TIME, font=FONT_SM, justify="left", anchor="w",
            text=(f"协议端点：https://{local_ip()}:{cfg.port}/api/localsend/v2\n"
                  f"证书：{info['path']}"),
        ).pack(anchor="w", padx=16, pady=(8, 0))

        manage = tk.Frame(self, bg=BG)
        manage.pack(fill="x", padx=16, pady=(10, 0))
        tk.Button(manage, text="管理已屏蔽设备 / 清空屏蔽列表",
                  command=self.app._open_blocked, bg=BG_HOVER, fg=FG_DIM,
                  font=FONT_SM, relief="flat", bd=0, padx=10, pady=4,
                  cursor="hand2").pack(side="left")

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", side="bottom", padx=16, pady=14)
        tk.Button(btns, text="保存", command=self._save, bg=ACCENT, fg="white",
                  font=FONT_UI, relief="flat", bd=0, padx=24, pady=5,
                  cursor="hand2").pack(side="right")
        tk.Button(btns, text="取消", command=self.destroy, bg=BG_HOVER, fg=FG,
                  font=FONT_UI, relief="flat", bd=0, padx=20, pady=5,
                  cursor="hand2").pack(side="right", padx=8)

    def _browse(self) -> None:
        d = filedialog.askdirectory(parent=self, title="选择接收目录",
                                    initialdir=str(self.app.cfg.download_dir))
        if d:
            self.e_dir.delete(0, "end")
            self.e_dir.insert(0, d)

    def _save(self) -> None:
        alias = self.e_alias.get().strip()
        if alias:
            self.app.cfg.alias = alias
            self.app._apply_window_title()
        d = self.e_dir.get().strip()
        if d:
            try:
                Path(d).mkdir(parents=True, exist_ok=True)
                self.app.cfg.set("download_dir", d)
            except Exception as e:
                messagebox.showerror("目录不可用", str(e), parent=self)
                return
        self.app._load_conversations()
        self.destroy()


class AddPeerDialog(tk.Toplevel):
    """手动按 IP 添加设备：彻底绕开发现机制。

    适用于路由器开了 AP 隔离、组播被过滤、或两端不在同一广播域的情形。
    """

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.title("手动添加设备")
        self.configure(bg=BG)
        self.geometry("480x220")
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()
        self._build()

    def _build(self) -> None:
        tk.Label(self, text="对方 IP 地址", bg=BG, fg=FG_DIM, font=FONT_SM).pack(
            anchor="w", padx=16, pady=(16, 4))
        self.e_ip = tk.Entry(self, bg=BG_INPUT, fg=FG, font=("Consolas", 11),
                             relief="flat", insertbackground=FG)
        self.e_ip.pack(fill="x", padx=16, ipady=6)
        self.e_ip.focus_set()
        self.e_ip.bind("<Return>", lambda e: self._ok())

        tk.Label(
            self, bg=BG, fg=FG_TIME, font=FONT_SM, anchor="w", justify="left",
            text=("在手机 LocalSend 的「设置 → 关于」或在路由器客户端列表里\n"
                  "可以看到它的 IP，例如 192.168.1.23。"),
        ).pack(anchor="w", padx=16, pady=(8, 0))

        self.lbl = tk.Label(self, text="", bg=BG, fg=WARN_ORANGE, font=FONT_SM, anchor="w")
        self.lbl.pack(anchor="w", padx=16)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", side="bottom", padx=16, pady=12)
        self.btn_ok = tk.Button(btns, text="连接", command=self._ok, bg=ACCENT, fg="white",
                                font=FONT_UI, relief="flat", bd=0, padx=24, pady=5,
                                cursor="hand2")
        self.btn_ok.pack(side="right")
        tk.Button(btns, text="取消", command=self.destroy, bg=BG_HOVER, fg=FG,
                  font=FONT_UI, relief="flat", bd=0, padx=20, pady=5,
                  cursor="hand2").pack(side="right", padx=8)

    def _ok(self) -> None:
        ip = self.e_ip.get().strip()
        if not ip:
            return
        self.lbl.configure(text="正在连接…", fg=WARN_ORANGE)
        self.btn_ok.configure(state="disabled")

        def worker():
            info = probe_ip(ip, port=DISCOVERY_PORT, timeout=4.0)
            self.app.bus.emit("manual_probe", ip=ip, info=info)

        import threading
        threading.Thread(target=worker, daemon=True).start()
        self._poll()

    def _poll(self) -> None:
        """非阻塞轮询：主循环不能被 time.sleep 卡住。"""
        res = getattr(self.app, "_manual_result", None)
        if res:
            self.app._manual_result = None
            ip_done, info = res
            if info:
                self.destroy()
                return
            self.btn_ok.configure(state="normal")
            self.lbl.configure(
                text=f"{ip_done} 无响应：确认已开 LocalSend、同一网段、防火墙放行",
                fg="#e05c5c")
            return
        if not self.winfo_exists():
            return
        self.after(150, self._poll)


class DiagDialog(tk.Toplevel):
    """诊断窗口：把「为什么发现不了」变成可见的数字。"""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.title("网络诊断")
        self.configure(bg=BG)
        self.geometry("620x520")
        self.transient(app)
        self._build()
        self._refresh()

    def _build(self) -> None:
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(head, text="故障排除", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        tk.Label(head, text="按顺序点一遍，多数「搜不到设备」都能解决", bg=BG,
                 fg=FG_TIME, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(10, 0))

        # ---- 两张操作卡片：自动修复 / 打开防火墙 ----
        cards = tk.Frame(self, bg=BG)
        cards.pack(fill="x", padx=16, pady=(2, 10))
        self._make_card(
            cards, "🛠", "自动修复",
            "一键放行端口、加入组播组、重新宣告本机。",
            "开始修复", self._auto_fix, primary=True)
        self._make_card(
            cards, "🛡", "打开防火墙",
            "为 Windows 防火墙添加 TCP/UDP 入站规则。",
            "打开防火墙设置", self._open_firewall_ui)

        tk.Label(self, text="发现状态", bg=BG, fg=FG, font=("Microsoft YaHei UI", 10, "bold")
                 ).pack(anchor="w", padx=16, pady=(2, 4))
        self.txt = tk.Text(self, bg=BG_INPUT, fg=FG, font=("Consolas", 9), relief="flat",
                           bd=0, wrap="word", padx=10, pady=8, height=12)
        self.txt.pack(fill="both", expand=True, padx=16)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=16, pady=12)
        tk.Button(btns, text="修复防火墙权限", command=self._fix_firewall, bg=ACCENT,
                  fg="white", font=FONT_UI, relief="flat", bd=0, padx=16, pady=5,
                  cursor="hand2").pack(side="left")
        tk.Button(btns, text="重新扫描", command=self._refresh, bg=BG_HOVER, fg=FG,
                  font=FONT_UI, relief="flat", bd=0, padx=16, pady=5,
                  cursor="hand2").pack(side="left", padx=8)
        tk.Button(btns, text="关闭", command=self.destroy, bg=BG_HOVER, fg=FG,
                  font=FONT_UI, relief="flat", bd=0, padx=20, pady=5,
                  cursor="hand2").pack(side="right")

    def _make_card(self, parent, icon: str, title: str, desc: str,
                   btn_text: str, cmd, *, primary: bool = False) -> None:
        """一张等宽卡片：图标 + 标题 + 说明 + 操作按钮。"""
        card = tk.Frame(parent, bg=BG_SIDE)
        card.pack(side="left", fill="both", expand=True, padx=(0, 8) if not primary else (0, 8))
        inner = tk.Frame(card, bg=BG_SIDE)
        inner.pack(fill="both", expand=True, padx=13, pady=11)

        top = tk.Frame(inner, bg=BG_SIDE)
        top.pack(fill="x")
        tk.Label(top, text=icon, bg=BG_SIDE, fg=ACCENT,
                 font=("Microsoft YaHei UI", 14)).pack(side="left")
        tk.Label(top, text=title, bg=BG_SIDE, fg=FG,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=(7, 0))

        tk.Label(inner, text=desc, bg=BG_SIDE, fg=FG_TIME,
                 font=("Microsoft YaHei UI", 8), anchor="w", justify="left",
                 wraplength=250).pack(fill="x", pady=(5, 8))

        bg = ACCENT if primary else BG_HOVER
        fg = "white" if primary else FG
        lb = tk.Label(inner, text=btn_text, bg=bg, fg=fg, font=FONT_SM,
                      padx=12, pady=5, cursor="hand2")
        lb.pack(anchor="w")
        lb.bind("<Button-1>", lambda e: cmd())
        hover = ACCENT_DARK if primary else BG_SEL
        lb.bind("<Enter>", lambda e: lb.configure(bg=hover))
        lb.bind("<Leave>", lambda e: lb.configure(bg=bg))

    def _auto_fix(self) -> None:
        """自动修复：重扫 + 重新宣告，并把结果写回状态区。

        真正的根因通常分两类——防火墙拦入站、或 Discovery 线程没在跑。
        前者交给 _fix_firewall（需要 UAC），后者这里直接重启即可。
        """
        steps: list[str] = []
        # 1) 重启发现器（UDP 监听可能因休眠/切网卡失效）
        try:
            self.app.discoverer.stop()
        except Exception:
            pass
        try:
            self.app.discoverer.start(self.app._on_peer)
            steps.append("✓ 已重启设备发现（组播 + 广播监听）")
        except Exception as e:
            steps.append(f"✗ 重启发现失败：{e}")
        # 2) 主动宣告一次，让对端立刻看到本机
        try:
            if hasattr(self.app.discoverer, "announce_now"):
                self.app.discoverer.announce_now()
                steps.append("✓ 已重新宣告本机信息")
        except Exception:
            pass
        # 3) 子网扫描（后台线程，不卡界面）
        try:
            import threading
            def work():
                try:
                    n = self.app.discoverer.scan_subnet()
                    self.app.bus.emit(
                        "progress",
                        text=f"自动修复：扫描完成，命中 {n} 台" if n else "自动修复：扫描完成，未发现新设备")
                except Exception as e:
                    self.app.bus.emit("progress", text=f"扫描出错：{e}")
            threading.Thread(target=work, daemon=True).start()
            steps.append("✓ 已启动子网扫描（1-254 逐个探测）")
        except Exception as e:
            steps.append(f"✗ 扫描失败：{e}")

        steps.append("")
        steps.append("若仍未发现设备，请点「打开防火墙」手动放行本程序。")
        self._refresh()
        self.txt.configure(state="normal")
        self.txt.insert("1.0", "【自动修复结果】\n" + "\n".join(steps) + "\n\n————————————————\n")
        self.txt.configure(state="disabled")
        messagebox.showinfo(
            "自动修复完成",
            "\n".join(s for s in steps if s.strip()),
            parent=self)

    def _open_firewall_ui(self) -> None:
        """打开 Windows 防火墙设置界面，让用户手动放行或查看规则。"""
        if os.name != "nt":
            messagebox.showinfo("仅 Windows", "该功能仅适用于 Windows。", parent=self)
            return
        ok = False
        for target in ("shell:::{4026492F-2F69-46B8-B9BF-5654FC07E423}",
                       "control.exe /name Microsoft.WindowsDefenderFirewall"):
            try:
                os.startfile(target) if target.startswith("shell:") else \
                    subprocess.Popen(["control.exe", "/name",
                                      "Microsoft.WindowsDefenderFirewall"])
                ok = True
                break
            except Exception:
                continue
        if not ok:
            try:
                subprocess.Popen(["control", "firewall.cpl"])
                ok = True
            except Exception:
                pass
        if ok:
            messagebox.showinfo(
                "已打开防火墙",
                "在左侧点「允许应用或功能通过…」，勾选本程序；\n"
                "或点「高级设置 → 入站规则 → 新建规则」放行 TCP/UDP 53317。",
                parent=self)
        else:
            messagebox.showerror("打开失败", "无法打开防火墙设置，请手动运行 control firewall.cpl。",
                                 parent=self)

    def _refresh(self) -> None:
        d = self.app.discoverer
        st = d.stats.as_dict()
        from .config import local_ip
        from . import __version__
        lines = [
            f"程序版本        : v{__version__}",
            f"本机 IP        : {local_ip()}",
            f"设备名称        : {self.app.cfg.alias}",
            f"HTTP 端口      : {self.app.server.port}",
            f"发现端口        : {st.get('bound_port')}（固定 53317）",
            f"组播加入成功    : {st.get('joined')} 个网卡",
            f"组播加入失败    : {st.get('join_fail')} 个网卡",
            f"宣告包已发送    : {st.get('sent')}",
            f"收到对端包      : {st.get('recv')}",
            f"扫描过的主机    : {st.get('scan_tried')}",
            f"扫描命中        : {st.get('scan_hit')}",
            f"当前在线设备    : {len(self.app.online)}",
            f"最后错误        : {st.get('last_error') or '无'}",
            "",
            "判断方法：",
            "  · 发包 > 0、收包 = 0  → 本机在发，但对端没回：多为防火墙拦截入站、",
            "    或路由器开了 AP 隔离 / 组播过滤。点下方「修复防火墙权限」。",
            "  · 组播加入失败 = 0 且 收包 = 0 → 网络栈或虚拟网卡异常，试试切到手机热点。",
            "  · 扫描命中 > 0 但列表为空 → 对端端口不是 53317，用「添加设备」填端口。",
            "",
        ]
        peers = [p for p in self.app.online.values() if p.get("ip")]
        if peers:
            lines.append("已发现设备：")
            for p in peers:
                lines.append(f"  {p.get('alias')}  {p.get('ip')}:{p.get('port')}"
                             f"  {p.get('deviceModel') or ''}")
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(lines))
        self.txt.configure(state="disabled")

    def _fix_firewall(self) -> None:
        """提权添加防火墙规则。

        自解压目录每次运行都变，路径规则对不上；这里改用「程序 + 端口」双规则，
        用户只需同意一次 UAC，之后固定目录的规则才真正持久生效。
        """
        if os.name != "nt":
            messagebox.showinfo("仅 Windows", "该功能仅适用于 Windows。", parent=self)
            return
        exe = sys.executable
        port = self.app.server.port
        script = (
            f'netsh advfirewall firewall add rule name="LocalSendChat TCP" '
            f'dir=in action=allow protocol=TCP localport=53317,{port} profile=private,domain\n'
            f'netsh advfirewall firewall add rule name="LocalSendChat UDP" '
            f'dir=in action=allow protocol=UDP localport=53317 profile=private,domain\n'
            f'netsh advfirewall firewall add rule name="LocalSendChat App" '
            f'dir=in action=allow program="{exe}" profile=private,domain\n'
            'echo DONE\npause\n'
        )
        tmp = Path(os.environ.get("TEMP", ".")) / "lsc_firewall.bat"
        try:
            tmp.write_text(script, encoding="mbcs")
        except Exception:
            tmp.write_text(script, encoding="utf-8")
        try:
            import ctypes
            # ShellExecuteW 的 runas 动词触发 UAC；不需要 subprocess 拼命令行
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "cmd.exe", f'/c "{tmp}"', None, 1)
            messagebox.showinfo(
                "已请求管理员权限",
                "请在弹窗里点「是」，规则添加完成后关闭黑窗口，再点「重新扫描」。",
                parent=self)
        except Exception as e:
            messagebox.showerror("失败", f"无法提权：{e}", parent=self)


class AboutDialog(tk.Toplevel):
    """关于：版本、功能、端口与上游声明。"""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        from . import __version__
        self.title("关于 LocalSend Chat")
        self.configure(bg=BG)
        self.geometry("470x370")
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()

        tk.Label(self, text=app.cfg.panel_title, bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(pady=(22, 2))
        tk.Label(self, text=f"版本 v{__version__}", bg=BG, fg=ACCENT,
                 font=("Consolas", 10)).pack()
        text = (
            "一个套在 LocalSend Protocol v2 之上的局域网聊天窗口。\n"
            "协议层未作改动，可与官方 LocalSend 客户端互相收发。\n\n"
            "· 纯局域网，无服务器、无账号、不上传云端\n"
            "· 聊天气泡、6 套配色、图片缩略图、一对多群发\n"
            "· 链接分享：手机浏览器直接下载文件\n\n"
            "53317 用于协议收发，53319 用于链接分享。\n"
            "聊天记录与配置位于 %APPDATA%\\LocalSendChat\\。\n\n"
            "本项目非 LocalSend 官方衍生版本，与上游无隶属关系。\n"
            "LocalSend 为 Apache-2.0 协议的开源项目。\n\n"
            "提示：双击版本号八次可修改面板名称。"
        )
        tk.Label(self, text=text, bg=BG, fg=FG_DIM, font=FONT_SM,
                 justify="left", anchor="w").pack(fill="x", padx=24, pady=(16, 0))
        tk.Button(self, text="关闭", command=self.destroy, bg=ACCENT, fg="white",
                  font=FONT_UI, relief="flat", bd=0, padx=26, pady=5,
                  cursor="hand2").pack(side="bottom", pady=14)


class BlockedDialog(tk.Toplevel):
    """已屏蔽设备：逐个恢复或一键清空。"""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.title("已屏蔽设备")
        self.configure(bg=BG)
        self.geometry("450x340")
        self.transient(app)
        self.grab_set()
        self._build()

    def _build(self) -> None:
        for w in list(self.winfo_children()):
            w.destroy()
        try:
            rows = self.app.store.blocked_peers()
        except Exception:
            rows = []
        tk.Label(self, text="被屏蔽的设备不会出现在列表里，刷新也不会回来。",
                 bg=BG, fg=FG_DIM, font=FONT_SM, anchor="w",
                 justify="left").pack(fill="x", padx=16, pady=(14, 8))

        box = tk.Frame(self, bg=BG)
        box.pack(fill="both", expand=True, padx=16)
        if not rows:
            tk.Label(box, text="（暂无屏蔽设备）", bg=BG, fg=FG_TIME,
                     font=FONT_SM).pack(pady=18)
        for r in rows:
            line = tk.Frame(box, bg=BG_INPUT)
            line.pack(fill="x", pady=3)
            alias = r.get("alias") or "未知设备"
            fp = str(r.get("fingerprint") or "")[:10]
            tk.Label(line, text=f"{alias}  ·  {fp}...", bg=BG_INPUT, fg=FG,
                     font=FONT_SM, anchor="w").pack(side="left", padx=10, pady=7)
            tk.Button(line, text="恢复", bg=BG_HOVER, fg=OK_GREEN,
                      font=FONT_SM, relief="flat", bd=0, padx=12,
                      cursor="hand2",
                      command=lambda f=r.get("fingerprint"): self._restore(f)
                      ).pack(side="right", padx=8)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", side="bottom", padx=16, pady=12)
        tk.Button(btns, text="一键全部恢复", command=self._restore_all,
                  bg=BG_HOVER, fg=FG, font=FONT_SM, relief="flat", bd=0,
                  padx=12, pady=5, cursor="hand2").pack(side="left")
        tk.Button(btns, text="关闭", command=self.destroy, bg=ACCENT,
                  fg="white", font=FONT_UI, relief="flat", bd=0, padx=22,
                  pady=5, cursor="hand2").pack(side="right")

    def _restore(self, fp: str) -> None:
        self.app._unblock_peer(fp)
        self._build()

    def _restore_all(self) -> None:
        self.app._clear_blocked()
        self._build()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
