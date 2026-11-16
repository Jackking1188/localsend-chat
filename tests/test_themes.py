# -*- coding: utf-8 -*-
"""v2.6.2 配色回归：主题齐全、槽位完整、对比度达标、樱雾粉保留、回退安全。"""
from __future__ import annotations

import unittest

from app import theme


REQUIRED_KEYS = (
    "label", "desc", "is_light", "bg", "side", "hover", "sel", "input",
    "fg", "dim", "time", "accent", "accent_h", "out_bg", "out_fg",
    "in_bg", "in_fg", "line", "green", "orange", "danger",
)

# 樱雾粉是用户明确要求原样保留的主题，其原始色值对比度较低，
# 不纳入新增主题的对比度严格要求；只校验其存在、槽位完整、key 未变。
CONTRAST_THEMES = [k for k in theme.ORDER if k != "sakura"]


def _lum(hex_color: str) -> float:
    """WCAG 相对亮度。"""
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
           for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


class ThemeTests(unittest.TestCase):
    def test_1_all_eight_present(self):
        self.assertEqual(len(theme.ORDER), 8)
        for key in ("graphite", "fluent_dark", "midnight", "classic",
                    "terminal", "fluent", "paper", "sakura"):
            self.assertIn(key, theme.THEMES, f"缺少主题 {key}")

    def test_2_sakura_kept(self):
        self.assertIn("sakura", theme.THEMES)
        self.assertEqual(theme.THEMES["sakura"]["label"], "樱雾粉")
        self.assertTrue(theme.is_light("sakura"))

    def test_3_order_matches_themes(self):
        self.assertEqual(set(theme.ORDER), set(theme.THEMES))
        self.assertEqual(len(theme.ORDER), len(set(theme.ORDER)))

    def test_4_every_theme_has_all_slots(self):
        for key, t in theme.THEMES.items():
            for slot in REQUIRED_KEYS:
                self.assertIn(slot, t, f"{key} 缺少槽位 {slot}")
            for slot in REQUIRED_KEYS:
                if slot in ("label", "desc"):
                    continue
                if slot == "is_light":
                    self.assertIsInstance(t[slot], bool)
                else:
                    self.assertRegex(t[slot], r"^#[0-9a-fA-F]{6}$",
                                     f"{key}.{slot} 不是合法色值: {t[slot]}")

    def test_5_body_text_contrast(self):
        # 正文与背景、正文与收到气泡：正文要清晰可读
        for key in CONTRAST_THEMES:
            t = theme.THEMES[key]
            self.assertGreaterEqual(_contrast(t["fg"], t["bg"]), 7.0,
                                    f"{key} 正文/背景对比不足")
            self.assertGreaterEqual(_contrast(t["in_fg"], t["in_bg"]), 7.0,
                                    f"{key} 收到气泡正文对比不足")

    def test_6_out_bubble_contrast(self):
        for key in CONTRAST_THEMES:
            t = theme.THEMES[key]
            self.assertGreaterEqual(_contrast(t["out_fg"], t["out_bg"]), 4.5,
                                    f"{key} 发出气泡文字对比不足")

    def test_7_secondary_text_contrast(self):
        # 次要文字（时间/提示）不低于 4.5:1
        for key in CONTRAST_THEMES:
            t = theme.THEMES[key]
            self.assertGreaterEqual(_contrast(t["time"], t["bg"]), 4.5,
                                    f"{key} 时间文字对比不足")
            self.assertGreaterEqual(_contrast(t["dim"], t["side"]), 4.5,
                                    f"{key} 次要文字对比不足")

    def test_8_default_is_light(self):
        self.assertEqual(theme.DEFAULT, "fluent")
        self.assertTrue(theme.is_light(theme.DEFAULT))
        self.assertIn(theme.DEFAULT, theme.THEMES)

    def test_9_invalid_name_falls_back(self):
        # 旧配置里的 moonshine/深空蓝等已删除，必须安全回退，不抛异常
        self.assertIs(theme.get("moonshine"), theme.THEMES[theme.DEFAULT])
        self.assertIs(theme.get(None), theme.THEMES[theme.DEFAULT])
        self.assertIs(theme.get("不存在的主题"), theme.THEMES[theme.DEFAULT])

    def test_10_siblings_grouping(self):
        dark = theme.siblings("graphite")
        light = theme.siblings("sakura")
        self.assertEqual(len(dark), 5)
        self.assertEqual(len(light), 3)
        self.assertNotIn("sakura", dark)
        self.assertNotIn("graphite", light)

    def test_11_no_old_names_left(self):
        for gone in ("teal", "aurora", "coffee", "moonshine"):
            self.assertNotIn(gone, theme.THEMES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
