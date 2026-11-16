"""配色主题。

界面所有颜色集中在这里，切换主题只需换一个字典。
每套主题都按对比度挑选：正文与气泡底色对比度不低于 4.5:1，
主要文字与背景尽量达到 7:1，长时间看也不累。

配色取材于 Windows 11 Fluent、经典 Windows 与 macOS 的系统典型色。
"""
from __future__ import annotations

DEFAULT = "fluent"      # Windows 11 浅色，默认最百搭

# 顺序即主题面板里的显示顺序：先深色，后浅色
ORDER = [
    "graphite",       # macOS 深色 · 石墨
    "fluent_dark",    # Windows 11 深色
    "midnight",       # macOS 深色 · 午夜蓝
    "classic",        # Windows 经典蓝（Win10 风）
    "terminal",       # 深色终端 / 荧光青
    "fluent",         # Windows 11 浅色（默认）
    "paper",          # macOS 浅色 · 纸张
    "sakura",         # 樱雾粉（保留）
]

THEMES: dict[str, dict] = {
    # ---------------- 深色系 ----------------
    "graphite": {
        "label": "石墨灰",
        "desc": "macOS 深色 · 中性石墨，久看不偏色",
        "is_light": False,
        "bg": "#1c1c1e",
        "side": "#252527",
        "hover": "#2f2f32",
        "sel": "#3a3a3f",
        "input": "#28282b",
        "fg": "#f5f5f7",
        "dim": "#a1a1a6",
        "time": "#838389",
        "accent": "#4aa8ff",
        "accent_h": "#3390e8",
        "out_bg": "#0a5fd6",
        "out_fg": "#ffffff",
        "in_bg": "#2c2c2e",
        "in_fg": "#f5f5f7",
        "line": "#38383a",
        "green": "#30d158",
        "orange": "#ff9f0a",
        "danger": "#ff5f57",
    },
    "fluent_dark": {
        "label": "Win11 深空",
        "desc": "Windows 11 深色 · 冷灰配系统蓝",
        "is_light": False,
        "bg": "#1f1f1f",
        "side": "#272727",
        "hover": "#333333",
        "sel": "#3d3d3d",
        "input": "#2b2b2b",
        "fg": "#f5f5f5",
        "dim": "#b5b5b5",
        "time": "#949494",
        "accent": "#60cdff",
        "accent_h": "#3db6ef",
        "out_bg": "#0f6cbd",
        "out_fg": "#ffffff",
        "in_bg": "#323232",
        "in_fg": "#f5f5f5",
        "line": "#3a3a3a",
        "green": "#6ccb5f",
        "orange": "#f6c76b",
        "danger": "#ff6b6b",
    },
    "midnight": {
        "label": "午夜蓝",
        "desc": "macOS 午夜色 · 深蓝底配靛紫",
        "is_light": False,
        "bg": "#10141f",
        "side": "#171c2b",
        "hover": "#212940",
        "sel": "#2b3552",
        "input": "#1b2133",
        "fg": "#f0f4ff",
        "dim": "#9aa8c8",
        "time": "#7d8cae",
        "accent": "#8b8bf5",
        "accent_h": "#7373e8",
        "out_bg": "#4f52c9",
        "out_fg": "#ffffff",
        "in_bg": "#232a40",
        "in_fg": "#f0f4ff",
        "line": "#2a3250",
        "green": "#4ade80",
        "orange": "#f5b455",
        "danger": "#ff6b8a",
    },
    "classic": {
        "label": "经典蓝",
        "desc": "Windows 经典蓝 · 熟悉的深海蓝",
        "is_light": False,
        "bg": "#0c1b2a",
        "side": "#11263a",
        "hover": "#1a3550",
        "sel": "#234466",
        "input": "#162e46",
        "fg": "#eaf3ff",
        "dim": "#9db8d4",
        "time": "#7b98b6",
        "accent": "#4aa8ff",
        "accent_h": "#2f90ea",
        "out_bg": "#0e5fa8",
        "out_fg": "#ffffff",
        "in_bg": "#1b334c",
        "in_fg": "#eaf3ff",
        "line": "#223e5a",
        "green": "#4ec9a0",
        "orange": "#eca945",
        "danger": "#f07272",
    },
    "terminal": {
        "label": "荧光青",
        "desc": "深色终端风 · 荧光青绿点缀",
        "is_light": False,
        "bg": "#0d1117",
        "side": "#131920",
        "hover": "#1c2530",
        "sel": "#243040",
        "input": "#161d26",
        "fg": "#e6edf3",
        "dim": "#9aa7b5",
        "time": "#7b8a99",
        "accent": "#2ee6a8",
        "accent_h": "#1cc993",
        "out_bg": "#0f7d59",
        "out_fg": "#ffffff",
        "in_bg": "#1b2530",
        "in_fg": "#e6edf3",
        "line": "#262f3a",
        "green": "#3fb950",
        "orange": "#d29922",
        "danger": "#f85149",
    },
    # ---------------- 浅色系 ----------------
    "fluent": {
        "label": "Win11 晴空",
        "desc": "Windows 11 浅色 · 干净利落，默认首选",
        "is_light": True,
        "bg": "#f5f5f5",
        "side": "#ececec",
        "hover": "#e0e0e0",
        "sel": "#d4e6f8",
        "input": "#ffffff",
        "fg": "#1a1a1a",
        "dim": "#5a5a5a",
        "time": "#6e6e6e",
        "accent": "#0067c0",
        "accent_h": "#00559e",
        "out_bg": "#0067c0",
        "out_fg": "#ffffff",
        "in_bg": "#ffffff",
        "in_fg": "#1a1a1a",
        "line": "#dcdcdc",
        "green": "#0f7b3f",
        "orange": "#a85c00",
        "danger": "#c42b1c",
    },
    "paper": {
        "label": "纸张白",
        "desc": "macOS 浅色 · 暖白纸张，温和护眼",
        "is_light": True,
        "bg": "#f5f5f7",
        "side": "#ededf0",
        "hover": "#e2e2e7",
        "sel": "#d3e3fb",
        "input": "#ffffff",
        "fg": "#1d1d1f",
        "dim": "#5e5e63",
        "time": "#6e6e73",
        "accent": "#0071e3",
        "accent_h": "#005bb8",
        "out_bg": "#0071e3",
        "out_fg": "#ffffff",
        "in_bg": "#ffffff",
        "in_fg": "#1d1d1f",
        "line": "#dedee3",
        "green": "#248a3d",
        "orange": "#b25000",
        "danger": "#d70015",
    },
    "sakura": {
        "label": "樱雾粉",
        "desc": "柔粉雾面 · 轻盈温柔",
        "is_light": True,
        "bg": "#fffafb",
        "side": "#fdf1f4",
        "hover": "#fbe7ee",
        "sel": "#f8d9e4",
        "input": "#ffffff",
        "fg": "#2c1e24",
        "dim": "#7f6470",
        "time": "#a8919b",
        "accent": "#e0558a",
        "accent_h": "#c94476",
        "out_bg": "#e0558a",
        "out_fg": "#ffffff",
        "in_bg": "#ffffff",
        "in_fg": "#2c1e24",
        "line": "#f4dee6",
        "green": "#2f9e68",
        "orange": "#c07a1e",
        "danger": "#d24545",
    },
}


def get(name: str | None) -> dict:
    """取主题；名字无效时回退默认，绝不抛异常。"""
    if name and name in THEMES:
        return THEMES[name]
    return THEMES[DEFAULT]


def label(name: str | None) -> str:
    return get(name).get("label", name or DEFAULT)


def is_light(name: str | None) -> bool:
    return bool(get(name).get("is_light"))


def siblings(name: str | None) -> list[str]:
    """同一明暗分组内的主题，用于「深色/浅色」分栏展示。"""
    light = is_light(name)
    return [k for k in ORDER if bool(THEMES[k].get("is_light")) == light]
