# -*- coding: utf-8 -*-
"""v2.6.5 回归：顶栏长状态文字不再盖住功能按钮。

只用隐藏 Tk root + 轻量替身，不启动完整 App、不碰真实数据库。
运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("LSC_HOME", tempfile.mkdtemp(prefix="lsc-v265-test-"))
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402
from app import ui  # noqa: E402

LONG = ("未发现设备（端口 53317，发包 36，收包 36）"
        "· 请确认与手机在同一个 Wi-Fi")


def _shown(label: ui._StatusLabel) -> str:
    """绕过 cget 覆写，读取真正画在界面上的文字。"""
    return tk.Label.cget(label, "text")


class StatusLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        # 需要真实几何信息才能验证「不重叠」，隐藏窗口拿到的全是 0
        cls.root.geometry("996x700")
        cls.root.deiconify()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.top = tk.Frame(self.root, width=996, height=58)
        self.top.pack_propagate(False)
        self.top.pack()
        self.label = ui._StatusLabel(self.top, text="●  就绪",
                                     font=ui.FONT_SM)

    def tearDown(self):
        self.top.destroy()

    def test_1_full_text_is_preserved_for_callers(self):
        self.label.configure(text=LONG)
        self.assertEqual(self.label.cget("text"), LONG)

    def test_2_long_text_is_truncated_when_space_is_tight(self):
        self.label._max_px = 140
        self.label.configure(text=LONG)
        shown = _shown(self.label)
        self.assertNotEqual(shown, LONG)
        self.assertTrue(shown.endswith("…"))
        self.assertLess(len(shown), len(LONG))

    def test_3_short_text_is_shown_verbatim(self):
        self.label._max_px = 140
        self.label.configure(text="已发送")
        self.assertEqual(_shown(self.label), "已发送")
        self.assertFalse(self.label._truncated)

    def test_4_refit_expands_and_shrinks_with_top_bar(self):
        self.label.configure(text=LONG)
        self.label._max_px = 90
        self.label._apply_text()
        narrow = len(_shown(self.label))
        self.label._max_px = 600
        self.label._apply_text()
        self.assertEqual(_shown(self.label), LONG)
        self.assertGreater(len(LONG), narrow)

    def test_5_no_overlap_with_right_side_buttons(self):
        """复刻截图里的 996px 顶栏：长状态不得压到 6 个按钮。"""
        top = self.top
        for text in ("关于", "设置", "诊断", "添加设备", "刷新设备", "⤴  链接分享"):
            tk.Label(top, text=text).pack(side="right", padx=3, pady=14)
        self.label.pack(side="right", padx=(4, 16))
        top.update_idletasks()
        self.label.refit()
        self.label.configure(text=LONG)
        top.update_idletasks()

        s1, s2 = self.label.winfo_x(), self.label.winfo_x() + self.label.winfo_width()
        for w in top.winfo_children():
            if w is self.label:
                continue
            w1, w2 = w.winfo_x(), w.winfo_x() + w.winfo_width()
            self.assertFalse(
                s1 < w2 and w1 < s2,
                f"状态文字 [{s1},{s2}] 与按钮 {w.cget('text')!r} [{w1},{w2}] 重叠")


class ScanStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.host = type("H", (), {})()
        self.host._rows = [("FP", {"fp": "FP", "alias": "手机", "online": False,
                                   "preview": "", "info": {}})]
        self.host.lbl_status = ui._StatusLabel(self.root, text="", font=ui.FONT_SM)
        self.host.lbl_net = tk.Label(self.root, text="", wraplength=252)
        self.host.cfg = type("C", (), {"port": 53317})()
        self.host._load_conversations = lambda: None

    def _scan(self, stats):
        ui.App._handle_scan_done(self.host, {
            "found": 0, "online": 0, "stats": stats})

    def test_6_zero_device_status_is_short(self):
        self._scan({"sent": 36, "recv": 36, "joined": 1, "bound_port": 53317})
        self.assertLessEqual(len(self.host.lbl_status.cget("text")), 20)
        self.assertNotIn("端口 53317", self.host.lbl_status.cget("text"))

    def test_7_details_move_to_sidebar_footer(self):
        self._scan({"sent": 36, "recv": 0, "joined": 1, "bound_port": 53317})
        detail = self.host.lbl_net.cget("text")
        self.assertIn("发包 36", detail)
        self.assertIn("收包 0", detail)
        self.assertIn("防火墙/AP 隔离", detail)

    def test_8_online_scan_restores_footer(self):
        self.host._rows = [("FP", {"fp": "FP", "alias": "手机", "online": True,
                                   "preview": "", "info": {}})]
        ui.App._handle_scan_done(self.host, {"found": 1, "online": 1, "stats": {}})
        self.assertIn("在线 1 台", self.host.lbl_status.cget("text"))
        self.assertNotIn("发包", self.host.lbl_net.cget("text"))


if __name__ == "__main__":
    unittest.main()
