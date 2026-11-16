"""Windows 原生拖放能力层（tkdnd / OLE2）。

职责边界：这里只做「拖放数据 <-> 程序数据」的转换，界面与发送逻辑都在
ui.py。tkinter / tkinterdnd2 一律在 enable() 里按需导入，因此本模块的纯
逻辑函数（parse_file_list / expand_paths / classify_drop / drag_payload）
可以在没有图形环境、也没装 tkdnd 的解释器里做单元测试。

为什么用 tkdnd：Tkinter 8.6 本身没有跨应用拖放。Windows 上要跟资源管理器、
记事本、浏览器互通，必须走 OLE2（CF_HDROP / CF_UNICODETEXT），tkdnd 是
Tk 官方生态里唯一成熟的实现，且随 exe 附带一个本地 DLL，运行时不联网。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

# 与 tkinterdnd2 导出的常量保持一致；这里重复定义是为了让纯逻辑层
# 不必依赖 tkinter 就能被测试。
DND_TEXT = "DND_Text"
DND_FILES = "DND_Files"
ACTION_COPY = "copy"
ACTION_REFUSE = "refuse_drop"

MAX_FILES_PER_DROP = 200          # 一次拖入最多收这么多文件，防止误拖整个盘
MAX_DIR_WALK = 5000               # 展开文件夹时的遍历上限

_VERSION = ""
_ENABLED = False


class DnDUnavailable(RuntimeError):
    """tkdnd 不可用；程序应当降级为「拖放关闭」而不是崩溃。"""


def _import_package():
    """依次在 sys.path、程序目录/vendor、程序目录里找 tkinterdnd2。"""
    global _PKG_DIR
    try:
        import tkinterdnd2
        return tkinterdnd2
    except Exception:
        pass

    here = Path(__file__).resolve().parent.parent
    # 打包 exe：vendor 与 app/ 同级；源码运行：放在工作区 .workbuddy/vendor，
    # 这样 git 仓库里只有纯 Python，二进制运行时不进版本库。
    for cand in (here / "vendor", here,
                 here.parent / ".workbuddy" / "vendor",
                 Path.cwd() / "vendor"):
        if not cand.is_dir():
            continue
        p = str(cand)
        if p not in sys.path:
            sys.path.insert(0, p)
        try:
            import tkinterdnd2
            _PKG_DIR = p
            return tkinterdnd2
        except Exception:
            continue
    raise DnDUnavailable("未找到 tkinterdnd2 / tkdnd 运行库")


_PKG_DIR = ""


def enable(root) -> str:
    """给已有的 Tk root 注入 tkdnd，返回版本号；失败抛 DnDUnavailable。

    tkinterdnd2 的 DnDWrapper 在导入时就把 drop_target_register /
    drag_source_register / dnd_bind 挂到了 tkinter.BaseWidget 上，
    再调用 require(root) 加载 Tcl 包即可，无需替换 App 的基类。
    """
    global _VERSION, _ENABLED
    _import_package()
    from tkinterdnd2 import TkinterDnD
    try:
        _VERSION = str(TkinterDnD.require(root))
    except Exception as e:
        raise DnDUnavailable(f"tkdnd 加载失败：{e}") from e
    _ENABLED = True
    return _VERSION


def version() -> str:
    return _VERSION


def enabled() -> bool:
    return _ENABLED


# ---------------------------------------------------------------- 数据解析
def _tcl_split(s: str) -> list[str]:
    """极简 Tcl list 解析：处理 {带空格的路径} 与反斜杠转义。

    Windows 的 CF_HDROP 经 tkdnd 传回的是 Tcl list 字符串，例如
    ``{C:/Program Files/a.txt} D:/照片 1.jpg``；这里不依赖 Tcl 解释器，
    便于在没有 GUI 的环境里测试。
    """
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    while i < len(s):
        ch = s[i]
        # Tcl 的花括号内禁止一切替换：C:\Users\... 里的反斜杠是路径分隔符，
        # 绝不能当转义符吃掉。只有裸项才需要 \ 转义处理。
        if ch == "\\" and depth == 0 and i + 1 < len(s):
            buf.append(s[i + 1])
            i += 2
            continue
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if ch.isspace() and depth == 0:
            if buf:
                items.append("".join(buf))
                buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        items.append("".join(buf))
    return items


def clean_path(raw) -> str:
    """规范化一个可能是文件路径的字符串，空值返回 ''。"""
    s = str(raw or "").strip().strip('"').strip("'").strip()
    # 个别源会把单项直接塞成 {路径}；容错去掉一层花括号
    if len(s) >= 2 and s[0] == "{" and s[-1] == "}":
        s = s[1:-1].strip()
    if not s:
        return ""
    if s.lower().startswith("file://"):
        try:
            u = urlparse(s)
            s = unquote(u.path)
            if u.netloc and u.netloc.lower() != "localhost":
                s = "//" + u.netloc + s
            if len(s) > 2 and s[0] == "/" and s[2] == ":":
                s = s[1:]
        except Exception:
            return ""
    try:
        return os.path.normpath(s.replace("/", "\\"))
    except Exception:
        return s


def parse_file_list(data) -> list[str]:
    """把 DND_Files 的数据解析成路径列表（不去重、不校验存在性）。"""
    if data is None:
        return []
    if isinstance(data, (list, tuple)):
        raw_items = [str(x) for x in data]
    else:
        s = str(data).strip()
        if not s:
            return []
        raw_items = _tcl_split(s)
    out = []
    for it in raw_items:
        p = clean_path(it)
        if p:
            out.append(p)
    return out


def expand_paths(paths) -> tuple[list[str], list[str]]:
    """把拖入项展开成可发送的文件列表。

    * 文件夹递归展开成里面的文件（有数量上限）；
    * 按绝对路径大小写不敏感去重；
    * 失效路径不静默丢弃，返回给 UI 提示。

    返回 (files, problems)。
    """
    files: list[str] = []
    problems: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> bool:
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            return True
        seen.add(key)
        files.append(p)
        return len(files) < MAX_FILES_PER_DROP

    for raw in paths or []:
        p = clean_path(raw)
        if not p:
            continue
        if not os.path.exists(p):
            problems.append(f"路径不存在：{p}")
            continue
        if os.path.isdir(p):
            walked = 0
            try:
                for root, _dirs, names in os.walk(p):
                    for name in names:
                        walked += 1
                        if walked > MAX_DIR_WALK:
                            problems.append(f"文件夹内容过多，只取前 {MAX_DIR_WALK} 项：{p}")
                            break
                        if not add(os.path.join(root, name)):
                            break
                    if walked > MAX_DIR_WALK or len(files) >= MAX_FILES_PER_DROP:
                        break
            except OSError as e:
                problems.append(f"无法读取文件夹 {p}：{e}")
        else:
            add(p)
        if len(files) >= MAX_FILES_PER_DROP:
            problems.append(f"一次最多拖入 {MAX_FILES_PER_DROP} 个文件，其余已忽略")
            break
    return files, problems


_LOCAL_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")


def _looks_like_local_path(s: str) -> bool:
    if not s or len(s) > 300:
        return False
    low = s.lower()
    if low.startswith(("http://", "https://", "ftp://", "mailto:")):
        return False
    return low.startswith("file://") or bool(_LOCAL_PATH_RE.match(s))


def split_drop_text(text) -> tuple[str, list[str]]:
    """纯文本拖入：若整段都是已存在的本地路径，就当文件处理。

    返回 (剩余文字, 文件列表)。只要有一行不是有效路径，就整段当文字，
    避免把一句「C:/ 什么的」聊天内容误判成文件。
    """
    s = str(text or "")
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if not lines or not all(_looks_like_local_path(ln) for ln in lines):
        return s, []
    paths = [clean_path(ln) for ln in lines]
    if not all(os.path.exists(p) for p in paths):
        return s, []
    files, _problems = expand_paths(paths)
    if not files:
        return s, []
    return "", files


def classify_drop(data, dtype: str = "") -> tuple[str, object]:
    """判断一次 Drop 是文件还是文字。

    返回 ("files", 路径列表) 或 ("text", 文本)。优先信事件声明的类型；
    类型缺失时「整串都是存在的路径」也算文件（兼容个别只给文本的源）。
    """
    t = str(dtype or "").upper()
    if "HDROP" in t or "FILE" in t or "FILEGROUP" in t:
        return "files", parse_file_list(data)
    if isinstance(data, (list, tuple)):
        items = [clean_path(x) for x in data]
        if items and all(os.path.exists(p) for p in items):
            return "files", items
        return "text", " ".join(str(x) for x in data)
    items = parse_file_list(data)
    if items and all(os.path.exists(p) for p in items):
        return "files", items
    return "text", str(data or "")


def drag_payload(*, kind: str, path: str = "", body: str = ""):
    """构造 <<DragInitCmd>> 的返回值 (action, type, data)。

    文件消息 -> DND_Files(CF_HDROP)，资源管理器收到即复制文件；
    文字消息 -> DND_Text(CF_UNICODETEXT)，可拖进记事本/Word。
    源不存在时返回 None，UI 会因此不注册拖出。
    """
    if kind == "file":
        p = clean_path(path)
        if not p or not os.path.isfile(p):
            return None
        return (ACTION_COPY, DND_FILES, (p,))
    text = str(body or "")
    if not text:
        return None
    return (ACTION_COPY, DND_TEXT, text)


# ---------------------------------------------------------------- 控件注册
def _safe_copy(cb, e):
    try:
        cb(e)
    except Exception:
        pass
    return ACTION_COPY


def register_drop_target(widget, on_enter, on_leave, on_drop) -> None:
    """把单个 Tk 控件注册成拖入目标；四个回调都允许抛异常（内部吞掉）。"""
    widget.drop_target_register(DND_FILES, DND_TEXT)
    widget.dnd_bind("<<DropEnter>>", lambda e: _safe_copy(on_enter, e))
    widget.dnd_bind("<<DropPosition>>", lambda e: ACTION_COPY)
    widget.dnd_bind("<<DropLeave>>", lambda e: _safe_copy(on_leave, e))
    widget.dnd_bind("<<Drop>>", lambda e: _safe_copy(on_drop, e))


class DropBinder:
    """共享版拖入绑定器。

    聊天记录可能有几百个气泡，若每个控件都用 dnd_bind 注册，会创建
    「控件数 × 4」个 Python Tcl 命令。这里只在 root 上注册 4 个命令，
    其余控件复用同一段 bind 脚本，渲染开销与气泡数量无关。
    """

    def __init__(self, root, on_enter, on_leave, on_drop) -> None:
        # tk.Tk 只继承 Misc/Wm，不是 BaseWidget，没有 DnDWrapper 注入的
        # _substitute_dnd / _register。用一个不显示的 Frame 当共享命令宿主，
        # 它随 root 一起销毁，Tcl 命令也会被自动注销。
        import tkinter as tk
        self._anchor = tk.Frame(root)
        subst = self._anchor._substitute_dnd
        fmt = self._anchor._subst_format_str_dnd
        jobs = (
            ("<<DropEnter>>", lambda e: on_enter(e)),
            ("<<DropPosition>>", lambda e: None),
            ("<<DropLeave>>", lambda e: on_leave(e)),
            ("<<Drop>>", lambda e: on_drop(e)),
        )
        self._scripts: dict[str, str] = {}
        for seq, cb in jobs:
            fid = self._anchor._register(
                lambda e, f=cb: _safe_copy(f, e), subst, needcleanup=True)
            self._scripts[seq] = f"{fid} {fmt}"

    def bind(self, widget) -> None:
        # 幂等：一个控件只注册一次。标记挂在 Python 对象上，控件销毁后
        # 新建的同名 path 是新对象，会自动重新注册，不会漏掉新气泡。
        if getattr(widget, "_lsc_dnd_bound", False):
            return
        # 直接调 tkdnd 的 Tcl 命令，绕开 Python mixin —— 这样连 root
        # 自身（没有 drop_target_register 方法）也能注册成拖入目标。
        path = str(widget)
        widget.tk.call("tkdnd::drop_target", "register", path,
                       (DND_FILES, DND_TEXT))
        for seq, script in self._scripts.items():
            widget.tk.call("bind", path, seq, script)
        try:
            widget._lsc_dnd_bound = True
        except Exception:
            pass


def register_drag_source(widget, payload_fn) -> None:
    """把任意 Tk 控件注册成拖出源；payload_fn 返回 drag_payload() 的结果。"""
    def _init(_e):
        try:
            payload = payload_fn()
        except Exception:
            payload = None
        return payload if payload else (ACTION_REFUSE, DND_TEXT, "")

    widget.tk.call("tkdnd::drag_source", "register", str(widget),
                   (DND_FILES, DND_TEXT), 1)
    widget.dnd_bind("<<DragInitCmd>>", _init)
