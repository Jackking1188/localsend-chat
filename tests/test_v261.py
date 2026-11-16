# -*- coding: utf-8 -*-
"""v2.6.1 回归：在线高亮、关于窗口说明、隐藏入口。

只用隐藏 Tk root + 轻量替身渲染设备行，不启动完整 App、不碰真实数据库。
运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("LSC_HOME", tempfile.mkdtemp(prefix="lsc-v261-test-"))
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402
from app import __version__, ui  # noqa: E402


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def _bg(w):
    try:
        return str(w.cget("bg")).lower()
    except Exception:
        return ""


class _FakeRowHost:
    """只提供 App._make_row 需要的接口。"""

    def __init__(self, root):
        self.side_inner = tk.Frame(root)
        self.current_fp = ""
        self._row_widgets = {}
        self.opened = []
        self._open_row = lambda r: self.opened.append(r)
        self._row_menu = lambda e, r: None
        self._row_hover = lambda b, f, on: None


class OnlineHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.host = _FakeRowHost(self.root)

    def tearDown(self):
        for w in self.host.side_inner.winfo_children():
            w.destroy()

    def _render(self, online: bool):
        row = {"fp": "FP-PHONE", "alias": "真我 GT7 Pro",
               "online": online, "preview": "", "info": {}}
        ui.App._make_row(self.host, row)
        return self.host._row_widgets["FP-PHONE"]

    def test_1_online_has_solid_badge_instead_of_tiny_dot(self):
        box = self._render(True)
        labels = [w for w in _walk(box) if isinstance(w, tk.Label)]
        badges = [w for w in labels if w.cget("text") == "● 在线"]
        self.assertEqual(len(badges), 1)
        b = badges[0]
        self.assertEqual(_bg(b), ui.OK_GREEN.lower())
        self.assertEqual(str(b.cget("fg")).lower(), "#ffffff")
        self.assertGreaterEqual(int(b.cget("padx")), 6)   # 实心胶囊，不是小点
        self.assertFalse([w for w in labels if w.cget("text") == "●"])

    def test_2_online_row_has_green_bar_and_avatar_ring(self):
        box = self._render(True)
        green = [w for w in _walk(box) if _bg(w) == ui.OK_GREEN.lower()]
        self.assertGreaterEqual(len(green), 3)   # 竖条 + 头像外圈 + 徽章
        bars = [w for w in green if w.winfo_class() == "Frame"
                and int(w.cget("width")) == 5]
        self.assertEqual(len(bars), 1)

    def test_3_online_name_is_highlighted_green(self):
        box = self._render(True)
        name = next(w for w in _walk(box)
                    if isinstance(w, tk.Label)
                    and w.cget("text") == "真我 GT7 Pro")
        self.assertEqual(str(name.cget("fg")).lower(), ui.OK_GREEN.lower())

    def test_4_offline_row_has_no_badge_no_green_bar(self):
        box = self._render(False)
        texts = [str(w.cget("text")) for w in _walk(box)
                 if isinstance(w, tk.Label)]
        self.assertNotIn("● 在线", texts)
        self.assertNotIn("●", texts)
        green = [w for w in _walk(box) if _bg(w) == ui.OK_GREEN.lower()]
        self.assertEqual(green, [])
        name = next(w for w in _walk(box)
                    if isinstance(w, tk.Label)
                    and w.cget("text") == "真我 GT7 Pro")
        self.assertEqual(str(name.cget("fg")).lower(), ui.FG_DIM.lower())

    def test_5_every_new_widget_is_clickable(self):
        box = self._render(True)
        for w in _walk(box):
            self.assertTrue(w.bind("<Button-1>"),
                            f"{w.winfo_class()} 未绑定点击，会出现点不动的死区")

    def test_6_badge_inherits_full_row_interaction(self):
        # 隐藏窗口下 Tk 不派发生成事件，改为直接核对徽章继承的行交互绑定：
        # 新增控件若漏绑，鼠标落在徽章上就会出现「点不动 / 菜单不出」的死区。
        box = self._render(True)
        badge = next(w for w in _walk(box)
                     if isinstance(w, tk.Label) and w.cget("text") == "● 在线")
        for seq in ("<Button-1>", "<Button-3>", "<Enter>", "<Leave>"):
            self.assertTrue(badge.bind(seq), f"徽章缺少 {seq} 绑定")


class AboutDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.root.cfg = types.SimpleNamespace(panel_title="LocalSend Chat")
        cls.root._on_title_double_click = lambda e=None: None

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_1_no_misleading_double_click_hint(self):
        # 关于窗口是模态框，隐藏 root 下 grab/transient 需替身
        with mock.patch.object(ui.tk.Toplevel, "grab_set",
                               lambda self: None), \
             mock.patch.object(ui.tk.Toplevel, "transient",
                               lambda self, master=None: None):
            dlg = ui.AboutDialog(self.root)
            try:
                labels = [w for w in _walk(dlg) if isinstance(w, tk.Label)]
                texts = [str(w.cget("text")) for w in labels]
                self.assertFalse(any("双击版本号八次" in t for t in texts))
                self.assertIn(f"版本 v{__version__}", texts)
                ver = next(w for w in labels
                           if w.cget("text") == f"版本 v{__version__}")
                self.assertTrue(ver.bind("<Double-Button-1>"))
            finally:
                dlg.destroy()

    def test_2_about_still_has_usage_and_version(self):
        with mock.patch.object(ui.tk.Toplevel, "grab_set",
                               lambda self: None), \
             mock.patch.object(ui.tk.Toplevel, "transient",
                               lambda self, master=None: None):
            dlg = ui.AboutDialog(self.root)
            try:
                texts = " ".join(str(w.cget("text")) for w in _walk(dlg)
                                 if isinstance(w, tk.Label))
                for needle in ("拖入", "拖出", "53317", __version__):
                    self.assertIn(needle, texts)
            finally:
                dlg.destroy()


class _ClickHost:
    """App._on_title_double_click 的轻量替身。"""

    def __init__(self, root):
        self._root = root
        self._title_clicks = 0
        self._title_click_ts = 0.0
        self.cfg = types.SimpleNamespace(
            admin_pass="qinhaibin", panel_title="LocalSend Chat")
        self.lbl_brand = tk.Label(root)
        self.lbl_status = tk.Label(root)
        self._apply_window_title = lambda: None

    def winfo_children(self):
        return self._root.winfo_children()


class HiddenTitleEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.host = _ClickHost(self.root)

    def _click8(self):
        for _ in range(8):
            ui.App._on_title_double_click(self.host)

    def test_1_eight_double_clicks_open_password_dialog(self):
        with mock.patch.object(ui.simpledialog, "askstring",
                               return_value=None) as ask:
            self._click8()
        self.assertEqual(ask.call_count, 1)

    def test_2_seven_clicks_do_nothing(self):
        with mock.patch.object(ui.simpledialog, "askstring") as ask:
            for _ in range(7):
                ui.App._on_title_double_click(self.host)
        ask.assert_not_called()

    def test_3_correct_password_changes_panel_title(self):
        with mock.patch.object(ui.simpledialog, "askstring",
                               side_effect=["qinhaibin", "新面板"]):
            self._click8()
        self.assertEqual(self.host.cfg.panel_title, "新面板")
        self.assertEqual(self.host.lbl_brand.cget("text"), "新面板")

    def test_4_wrong_password_is_rejected(self):
        with mock.patch.object(ui.simpledialog, "askstring",
                               return_value="wrong"), \
             mock.patch.object(ui.messagebox, "showerror") as err:
            self._click8()
        err.assert_called_once()
        self.assertEqual(self.host.cfg.panel_title, "LocalSend Chat")


class VersionTests(unittest.TestCase):
    def test_version_is_265(self):
        self.assertEqual(__version__, "2.6.5")


if __name__ == "__main__":
    unittest.main(verbosity=2)
