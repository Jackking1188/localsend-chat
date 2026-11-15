"""启动入口。

单文件自解压运行时没有控制台，因此这里做三件事：
  1. 所有异常写入 %APPDATA%\\LocalSendChat\\startup.log，便于排查；
  2. 有控制台时打印堆栈，无控制台时弹窗告知日志位置；
  3. 引导 sys.path，兼容「程序目录 / 自解压临时目录」两种情形。
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _log_dir() -> Path:
    override = os.environ.get("LSC_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "LocalSendChat"
    return Path.home() / ".localsendchat"


def _write_log(text: str) -> str:
    try:
        d = _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / "startup.log"
        p.write_text(text, encoding="utf-8")
        return str(p)
    except Exception:
        return ""


def _fatal(msg: str, detail: str) -> int:
    log_path = _write_log(detail)
    has_console = sys.stderr is not None and sys.stderr.isatty()
    if has_console:
        print(msg)
        print(detail)
        try:
            input("\n按回车关闭…")
        except Exception:
            pass
    else:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "LocalSend Chat 无法启动",
                f"{msg}\n\n日志：{log_path or '（写入失败）'}\n\n"
                f"以下为前 800 字符：\n{detail[:800]}",
            )
            root.destroy()
        except Exception:
            pass
    return 1


def main() -> int:
    try:
        from app.ui import main as run_ui
    except Exception:
        return _fatal("程序文件不完整或损坏。", traceback.format_exc())

    try:
        run_ui()
    except Exception:
        return _fatal("运行过程中出现错误。", traceback.format_exc())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
