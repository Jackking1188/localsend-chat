# -*- coding: utf-8 -*-
"""v2.6.0 拖放回归测试。

纯逻辑用例不依赖图形环境；Tk 用例只用一个隐藏 root，不启动完整 App、
不读写真实配置与聊天记录。运行：

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from app import dnd

TMP = tempfile.mkdtemp(prefix="lsc-dnd-test-")


def _mkfile(name: str) -> str:
    p = Path(TMP) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    return str(p)


class ParseTests(unittest.TestCase):
    """CF_HDROP 经 tkdnd 传回的是 Tcl list 字符串。"""

    def test_1_braces_keep_spaces(self):
        self.assertEqual(
            dnd._tcl_split("{C:/Program Files/a.txt} D:/b.jpg"),
            ["C:/Program Files/a.txt", "D:/b.jpg"])

    def test_2_windows_backslashes_are_not_escapes(self):
        raw = r"{C:\Users\Administrator\照片 1.jpg}"
        self.assertEqual(dnd._tcl_split(raw),
                         [r"C:\Users\Administrator\照片 1.jpg"])
        self.assertEqual(dnd.clean_path(raw),
                         r"C:\Users\Administrator\照片 1.jpg")

    def test_3_parse_file_list_variants(self):
        f = _mkfile("中文 名.txt")
        self.assertEqual(dnd.parse_file_list("{" + f + "}"), [f])
        self.assertEqual(dnd.parse_file_list([f, f]), [f, f])
        self.assertEqual(dnd.parse_file_list(None), [])
        self.assertEqual(dnd.parse_file_list(""), [])

    def test_4_file_uri(self):
        self.assertEqual(
            dnd.clean_path("file:///C:/Users/Administrator/a%20b.txt"),
            r"C:\Users\Administrator\a b.txt")


class ExpandTests(unittest.TestCase):
    def test_1_file_dedup_and_folder_expand(self):
        f = _mkfile("dup.txt")
        sub = Path(TMP) / "folder"
        sub.mkdir(exist_ok=True)
        (sub / "inside.bin").write_bytes(b"y")
        files, problems = dnd.expand_paths([f, f, str(sub), str(sub)])
        self.assertEqual(len(files), 2)
        self.assertIn(f, files)
        self.assertTrue(any(p.endswith("inside.bin") for p in files))
        self.assertEqual(problems, [])

    def test_2_missing_paths_are_reported(self):
        ghost = str(Path(TMP) / "not-here.txt")
        files, problems = dnd.expand_paths([ghost])
        self.assertEqual(files, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("路径不存在", problems[0])

    def test_3_drop_limit(self):
        a, b, c = (_mkfile(n) for n in ("a.txt", "b.txt", "c.txt"))
        with mock.patch.object(dnd, "MAX_FILES_PER_DROP", 2):
            files, problems = dnd.expand_paths([a, b, c])
        self.assertEqual(len(files), 2)
        self.assertTrue(any("最多" in p for p in problems))


class ClassifyTests(unittest.TestCase):
    def test_1_hdrop_is_files(self):
        f = _mkfile("drop.txt")
        kind, data = dnd.classify_drop("{" + f + "}", "CF_HDROP")
        self.assertEqual(kind, "files")
        self.assertEqual(data, [f])

    def test_2_unicode_text_stays_text(self):
        kind, data = dnd.classify_drop("今天天气不错 哈哈", "CF_UNICODETEXT")
        self.assertEqual((kind, data), ("text", "今天天气不错 哈哈"))

    def test_3_paths_without_declared_type(self):
        f = _mkfile("untyped.txt")
        self.assertEqual(dnd.classify_drop("{" + f + "}", ""), ("files", [f]))

    def test_4_url_is_not_a_file(self):
        kind, data = dnd.classify_drop("https://example.com/a.jpg", "")
        self.assertEqual(kind, "text")


class SplitTextTests(unittest.TestCase):
    def test_1_all_existing_paths_become_files(self):
        a, b = _mkfile("p1.txt"), _mkfile("p2.txt")
        text, files = dnd.split_drop_text(a + "\n" + b)
        self.assertEqual(text, "")
        self.assertEqual(files, [a, b])

    def test_2_normal_sentence_stays_text(self):
        s = "C:/abc 不是路径，只是聊天内容"
        self.assertEqual(dnd.split_drop_text(s), (s, []))

    def test_3_mixed_lines_are_not_misjudged(self):
        a = _mkfile("mixed.txt")
        s = a + "\n顺带说一句"
        self.assertEqual(dnd.split_drop_text(s), (s, []))


class DragPayloadTests(unittest.TestCase):
    def test_1_file_payload_uses_dnd_files(self):
        f = _mkfile("out.bin")
        action, kind, data = dnd.drag_payload(kind="file", path=f)
        self.assertEqual(action, dnd.ACTION_COPY)
        self.assertEqual(kind, dnd.DND_FILES)
        self.assertEqual(tuple(data), (f,))

    def test_2_missing_source_disables_drag(self):
        self.assertIsNone(dnd.drag_payload(
            kind="file", path=str(Path(TMP) / "gone.bin")))

    def test_3_text_payload(self):
        action, kind, data = dnd.drag_payload(kind="text", body="你好")
        self.assertEqual((action, kind, data),
                         (dnd.ACTION_COPY, dnd.DND_TEXT, "你好"))

    def test_4_empty_text_disables_drag(self):
        self.assertIsNone(dnd.drag_payload(kind="text", body=""))


class TkBindTests(unittest.TestCase):
    """真实 tkdnd 绑定：主窗口与子控件都能拖入，气泡能注册拖出。"""

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
        except Exception as e:
            raise unittest.SkipTest(f"无 tkinter：{e}")
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
            cls.ver = dnd.enable(cls.root)
        except dnd.DnDUnavailable as e:
            try:
                cls.root.destroy()
            except Exception:
                pass
            cls.root = None
            raise unittest.SkipTest(f"tkdnd 不可用：{e}")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "root", None) is not None:
            cls.root.destroy()

    def test_1_enable(self):
        self.assertTrue(dnd.enabled())
        self.assertTrue(self.ver)

    def test_2_shared_binder_covers_root_and_child(self):
        import tkinter as tk
        seen = []
        b = dnd.DropBinder(self.root,
                           lambda e: seen.append("enter"),
                           lambda e: seen.append("leave"),
                           lambda e: seen.append("drop"))
        child = tk.Frame(self.root)
        for w in (self.root, child):
            b.bind(w)
            types = w.tk.call("bind", str(w), "<<DropTargetTypes>>")
            self.assertIn("CF_HDROP", str(types))
            self.assertIn("CF_UNICODETEXT", str(types))
            self.assertTrue(w.tk.call("bind", str(w), "<<Drop>>"))
        self.assertEqual(len(b._scripts), 4)
        child.destroy()

    def test_3_drag_source_registered(self):
        import tkinter as tk
        f = _mkfile("drag.txt")
        w = tk.Frame(self.root)
        dnd.register_drag_source(
            w, lambda: dnd.drag_payload(kind="file", path=f))
        self.assertTrue(w.tk.call("bind", str(w), "<<DragInitCmd>>"))
        self.assertIn("CF_HDROP",
                      str(w.tk.call("bind", str(w), "<<DragSourceTypes>>")))
        w.destroy()


class SendOrderTests(unittest.TestCase):
    """文字 + 附件必须在同一个后台任务里串行发送（避免对端 409）。"""

    @classmethod
    def setUpClass(cls):
        try:
            from app import ui
            cls.ui = ui
        except Exception as e:
            raise unittest.SkipTest(f"无法导入 ui：{e}")

    def _run(self, text, attachments):
        ui = self.ui
        order = []
        ok = types.SimpleNamespace(ok=True, message="", is_pin_required=False,
                                   is_busy=False, is_declined=False)

        def fake_send_text(*_a, **_k):
            order.append("text")
            return ok

        def fake_send_files(*_a, **_k):
            order.append("files")
            return [(os.path.basename(p), ok) for p in attachments]

        fake = types.SimpleNamespace()
        fake.text = mock.Mock()
        fake.text.get.return_value = text
        fake._attachments = list(attachments)
        fake._current_peer = lambda: {"alias": "测试设备", "ip": "127.0.0.1"}
        fake.lbl_status = mock.Mock()
        fake.update_idletasks = lambda: None
        fake.current_fp = "FP"
        fake.cfg = types.SimpleNamespace(alias="我")
        fake._my_info = lambda: {"alias": "我"}
        fake._render_history = lambda: None
        fake._render_sidebar = lambda: None
        fake._clear_attachments = lambda: fake._attachments.clear()
        # 走真实的文件发送分支，只把网络层替换成替身
        fake._send_paths_sync = lambda paths, target: ui.App._send_paths_sync(
            fake, paths, target)
        fake.store = mock.Mock()
        fake.store.add_message.return_value = {"id": 1}
        fake.bus = mock.Mock()

        class SyncThread:
            def __init__(self, target=None, daemon=None):
                self._target = target

            def start(self):
                self._target()

        with mock.patch.object(ui.protocol, "send_text", fake_send_text), \
             mock.patch.object(ui.protocol, "send_files", fake_send_files), \
             mock.patch("threading.Thread", SyncThread):
            ui.App._send_text(fake)
        return order, fake

    def test_1_text_then_files_in_one_worker(self):
        f = _mkfile("send-a.txt")
        order, fake = self._run("你好", [f])
        self.assertEqual(order, ["text", "files"])
        self.assertEqual(fake._attachments, [])
        self.assertTrue(fake.store.add_message.called)

    def test_2_files_only_still_sends(self):
        f = _mkfile("send-b.txt")
        order, fake = self._run("", [f])
        self.assertEqual(order, ["files"])
        # 没有文字消息，但成功发出的文件仍要落库成一条文件气泡
        self.assertTrue(fake.store.add_message.called)
        self.assertEqual(fake.store.add_message.call_args.kwargs.get("kind"),
                         "file")

    def test_3_nothing_to_send_does_nothing(self):
        order, _ = self._run("", [])
        self.assertEqual(order, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
