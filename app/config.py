"""路径与配置管理。

数据目录必须持久化：自解压单文件每次运行会把程序解到临时目录，
因此证书、数据库、配置一律放在用户目录下，而不是程序目录。
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

APP_NAME = "LocalSend Chat"

# 程序自身所在目录（自解压后为临时目录，可能被清理）
BASE_DIR = Path(__file__).resolve().parent.parent


def _home() -> Path:
    """持久数据目录。可用环境变量覆盖，便于测试与多实例。"""
    override = os.environ.get("LSC_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(root) / "LocalSendChat"
    return Path.home() / ".localsendchat"


HOME_DIR = _home()
DATA_DIR = HOME_DIR
CERT_DIR = DATA_DIR / "certs"
DB_PATH = DATA_DIR / "chat.db"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS = {
    "alias": "",
    "port": 53317,
    "auto_accept": True,
    "download_dir": "",
    "device_model": "LocalSend Chat",
    "announce_interval": 6,
    "history_limit": 500,
    "ui_port": 0,
    "theme": "",
}


def _default_download_dir() -> str:
    """优先放到用户真实的下载文件夹，便于找到收到的文件。"""
    try:
        if os.name == "nt":
            profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
            downloads = Path(profile) / "Downloads" / APP_NAME
            return str(downloads)
        dl = Path.home() / "Downloads" / APP_NAME
        return str(dl)
    except Exception:
        return str(DATA_DIR / "downloads")


def ensure_dirs() -> None:
    for d in (DATA_DIR, CERT_DIR):
        d.mkdir(parents=True, exist_ok=True)


class Config:
    def __init__(self) -> None:
        ensure_dirs()
        self._data = dict(DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data.update(loaded)
            except Exception:
                pass
        if not self._data.get("alias"):
            self._data["alias"] = default_alias()
        if not self._data.get("download_dir"):
            self._data["download_dir"] = _default_download_dir()
        try:
            Path(self._data["download_dir"]).mkdir(parents=True, exist_ok=True)
        except Exception:
            self._data["download_dir"] = str(DATA_DIR / "downloads")
            Path(self._data["download_dir"]).mkdir(parents=True, exist_ok=True)

    # --- 通用读写 ---
    def get(self, key: str):
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def save(self) -> None:
        try:
            CONFIG_PATH.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # --- 常用项 ---
    @property
    def alias(self) -> str:
        return self._data.get("alias") or default_alias()

    @alias.setter
    def alias(self, v: str) -> None:
        self.set("alias", v.strip() or default_alias())

    @property
    def port(self) -> int:
        try:
            return int(self._data.get("port") or 53317)
        except Exception:
            return 53317

    @property
    def auto_accept(self) -> bool:
        return bool(self._data.get("auto_accept", True))

    @property
    def download_dir(self) -> Path:
        p = Path(self._data.get("download_dir") or (DATA_DIR / "downloads"))
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return p

    @property
    def announce_interval(self) -> float:
        try:
            return max(2.0, float(self._data.get("announce_interval") or 6))
        except Exception:
            return 6.0

    @property
    def history_limit(self) -> int:
        try:
            return max(20, int(self._data.get("history_limit") or 500))
        except Exception:
            return 500


def default_alias() -> str:
    name = ""
    try:
        name = socket.gethostname()
    except Exception:
        pass
    name = (name or "LocalSend").strip()
    return name[:28] or "LocalSend"


def local_ip() -> str:
    """取本机局域网地址（不发包，只查路由表）。"""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def is_windows() -> bool:
    return os.name == "nt" and sys.platform.startswith("win")
