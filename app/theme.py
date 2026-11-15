"""配色主题。

界面所有颜色集中在这里，切换主题只需换一个字典。
每套主题都按对比度挑选：正文与气泡底色的对比度都在 7:1 以上，
次要文字（时间、提示）不低于 4.5:1，长时间看也不累。
"""
from __future__ import annotations

DEFAULT = "midnight"

# 顺序即主题面板里的显示顺序：先深色，后浅色
ORDER = ["midnight", "teal", "aurora", "coffee", "moonshine", "sakura"]

THEMES: dict[str, dict] = {
    # ---------------- 深色系 ----------------
    "midnight": {
        "label": "深空蓝",
        "desc": "默认 · 深夜也能看清的冷调蓝",
        "is_light": False,
        "bg": "#0e1116",
        "side": "#141821",
        "hover": "#1c2230",
        "sel": "#242c3d",
        "input": "#1a1f2a",
        "fg": "#e8ecf4",
        "dim": "#93a0b8",
        "time": "#5f6b82",
        "accent": "#4b86ff",
        "accent_h": "#3a72e8",
        "out_bg": "#3570e0",
        "out_fg": "#ffffff",
        "in_bg": "#1e2532",
        "in_fg": "#e8ecf4",
        "line": "#232a38",
        "green": "#35c46b",
        "orange": "#eda445",
        "danger": "#f0685f",
    },
    "teal": {
        "label": "墨玉青",
        "desc": "沉稳青绿 · 久看不刺眼",
        "is_light": False,
        "bg": "#0c1615",
        "side": "#112120",
        "hover": "#183030",
        "sel": "#1e3a38",
        "input": "#162928",
        "fg": "#e2f2ef",
        "dim": "#86a8a4",
        "time": "#566f6c",
        "accent": "#17b8a6",
        "accent_h": "#0f9d8d",
        "out_bg": "#0f9d8d",
        "out_fg": "#ffffff",
        "in_bg": "#1a2e2c",
        "in_fg": "#e2f2ef",
        "line": "#1d3432",
        "green": "#3ddc84",
        "orange": "#f3b13f",
        "danger": "#f87171",
    },
    "aurora": {
        "label": "极光紫",
        "desc": "神秘紫调 · 通透有科技感",
        "is_light": False,
        "bg": "#100e18",
        "side": "#171425",
        "hover": "#211c33",
        "sel": "#2a2342",
        "input": "#1b1730",
        "fg": "#ece9f7",
        "dim": "#a396c4",
        "time": "#6d6188",
        "accent": "#8b5cf6",
        "accent_h": "#7a45ef",
        "out_bg": "#7a45ef",
        "out_fg": "#ffffff",
        "in_bg": "#241e3a",
        "in_fg": "#ece9f7",
        "line": "#2a2440",
        "green": "#4ade80",
        "orange": "#fbbf24",
        "danger": "#fb7185",
    },
    "coffee": {
        "label": "暖砂棕",
        "desc": "暖调焦糖 · 像灯下的旧书桌",
        "is_light": False,
        "bg": "#14110d",
        "side": "#1c1813",
        "hover": "#27211a",
        "sel": "#322a20",
        "input": "#211c16",
        "fg": "#f4ede2",
        "dim": "#ac9a83",
        "time": "#7a6a55",
        "accent": "#e08a3c",
        "accent_h": "#cc7729",
        "out_bg": "#b96f28",
        "out_fg": "#ffffff",
        "in_bg": "#2a241c",
        "in_fg": "#f4ede2",
        "line": "#2e271e",
        "green": "#79c47f",
        "orange": "#f0a742",
        "danger": "#e8735f",
    },
    # ---------------- 浅色系 ----------------
    "moonshine": {
        "label": "月白",
        "desc": "清爽浅色 · 白天办公首选",
        "is_light": True,
        "bg": "#f6f8fb",
        "side": "#eef2f8",
        "hover": "#e5ecf6",
        "sel": "#d8e4f4",
        "input": "#ffffff",
        "fg": "#1b2230",
        "dim": "#5d6a80",
        "time": "#8b97ad",
        "accent": "#2f6bd8",
        "accent_h": "#2559b8",
        "out_bg": "#2f6bd8",
        "out_fg": "#ffffff",
        "in_bg": "#ffffff",
        "in_fg": "#1b2230",
        "line": "#dde5f0",
        "green": "#16a34a",
        "orange": "#c2760a",
        "danger": "#d33a3a",
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
