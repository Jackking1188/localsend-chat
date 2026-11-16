# -*- coding: utf-8 -*-
"""v2.6.3 回归：恢复原版 LocalSend「拖入即发送」，双向拖放不回退。

用轻量替身模拟 App，不启动 GUI、不触碰真实配置与本机端口。运行：

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from app import dnd, ui

TMP = tempfile.mkdtemp(prefix="lsc-v263-")
PEER = {"alias": "测试手机", "ip": "192.168.1.96", "port": 53317}


def _mkfile(name: str) -> str:
    p = Path(TMP) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"v263")
    return str(p)


def _fake_app(peer=None):
    fake = types.SimpleNamespace()
    fake.lbl_status = mock.Mock()
    fake.text = mock.Mock()
    fake.text.get.return_value = ""
    fake._attachments = []
    fake._dnd_leave = mock.Mock()
    fake._render_attachments = mock.Mock()
    fake._current_peer = lambda: peer

    def attach(paths):
        added = 0
        for p in paths or []:
            if p not in fake._attachments:
                fake._attachments.append(p)
                added += 1
        return added

    fake._add_attachments = attach
    fake._send_paths_async = mock.Mock()
    # 只把网络/控件替身换掉，真实业务逻辑仍走 App 的实现
    fake._send_dropped_files = lambda paths: ui.App._send_dropped_files(fake, paths)
    return fake


def _drop(data, dtype="CF_HDROP"):
    return types.SimpleNamespace(data=data, type=dtype)


class DropToSendTests(unittest.TestCase):
    """原版核心行为：文件拖进窗口，松手即走发送链路。"""

    def test_1_file_drop_sends_immediately(self):
        f = _mkfile("drop-send.txt")
        app = _fake_app(PEER)
        with mock.patch.object(dnd, "classify_drop", return_value=("files", [f])):
            ui.App._dnd_drop(app, _drop("{" + f + "}"))
        app._send_paths_async.assert_called_once_with([f], PEER)
        self.assertEqual(app._attachments, [])
        self.assertIn("已直接发送 1 个文件",
                      app.lbl_status.configure.call_args.kwargs.get("text", ""))

    def test_2_no_peer_stages_instead_of_losing_file(self):
        f = _mkfile("drop-staged.txt")
        app = _fake_app(None)
        with mock.patch.object(dnd, "classify_drop", return_value=("files", [f])):
            ui.App._dnd_drop(app, _drop("{" + f + "}"))
        app._send_paths_async.assert_not_called()
        self.assertEqual(app._attachments, [f])
        status = app.lbl_status.configure.call_args.kwargs.get("text", "")
        self.assertIn("暂存", status)
        self.assertIn("选设备", status)

    def test_3_text_drop_still_goes_to_input(self):
        app = _fake_app(PEER)
        with mock.patch.object(dnd, "classify_drop",
                               return_value=("text", "你好，晚上见")):
            ui.App._dnd_drop(app, _drop("你好，晚上见", "CF_UNICODETEXT"))
        app._send_paths_async.assert_not_called()
        app.text.insert.assert_called_with("end", "你好，晚上见")
        self.assertEqual(app._attachments, [])

    def test_4_path_text_is_treated_as_file(self):
        f = _mkfile("path-text.txt")
        app = _fake_app(PEER)
        with mock.patch.object(dnd, "classify_drop", return_value=("text", f)):
            ui.App._dnd_drop(app, _drop(f, "CF_UNICODETEXT"))
        app._send_paths_async.assert_called_once_with([f], PEER)
        app.text.insert.assert_not_called()

    def test_5_staged_files_are_removed_when_sent(self):
        f = _mkfile("dedup.txt")
        app = _fake_app(PEER)
        app._attachments = [f, _mkfile("other.txt")]
        sent = ui.App._send_dropped_files(app, [f])
        self.assertEqual(sent, 1)
        self.assertNotIn(f, app._attachments)
        self.assertEqual(len(app._attachments), 1)
        app._send_paths_async.assert_called_once_with([f], PEER)

    def test_6_missing_file_is_not_sent(self):
        app = _fake_app(PEER)
        ghost = str(Path(TMP) / "gone.txt")
        self.assertEqual(ui.App._send_dropped_files(app, [ghost]), 0)
        app._send_paths_async.assert_not_called()

    def test_7_folder_drop_expands_before_sending(self):
        folder = Path(TMP) / "album"
        folder.mkdir(exist_ok=True)
        a, b = folder / "a.jpg", folder / "b.jpg"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        app = _fake_app(PEER)
        with mock.patch.object(dnd, "classify_drop", return_value=("files", [str(folder)])):
            ui.App._dnd_drop(app, _drop("{" + str(folder) + "}"))
        args = app._send_paths_async.call_args.args[0]
        self.assertEqual(sorted(args), sorted([str(a), str(b)]))


class DragOutTests(unittest.TestCase):
    """反方向：聊天里的文件仍可拖到外部复制，文字仍可拖成纯文本。"""

    def test_1_file_bubble_payload_is_hdrop(self):
        f = _mkfile("out.bin")
        payload = dnd.drag_payload(kind="file", path=f)
        self.assertEqual(payload[0], dnd.ACTION_COPY)
        self.assertEqual(payload[1], dnd.DND_FILES)
        self.assertEqual(tuple(payload[2]), (f,))

    def test_2_text_bubble_payload_is_unicode_text(self):
        payload = dnd.drag_payload(kind="text", body="复制这段话")
        self.assertEqual(payload, (dnd.ACTION_COPY, dnd.DND_TEXT, "复制这段话"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
