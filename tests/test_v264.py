# -*- coding: utf-8 -*-
"""v2.6.4 回归：.txt 文件必须显示正文，不能把 base64 当词发出去。"""
from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import protocol

TMP = Path(tempfile.mkdtemp(prefix="lsc-v264-"))
CONTENT = "LSC-DND-OUT-VERIFY-20261115"


class TextPreviewTests(unittest.TestCase):
    def test_1_text_plain_preview_is_plaintext(self):
        meta = protocol._file_meta("fid", "拖出验证.txt", "text/plain",
                                   CONTENT.encode("utf-8"))
        self.assertEqual(meta["preview"], CONTENT)
        self.assertNotEqual(meta["preview"],
                            base64.b64encode(CONTENT.encode()).decode())

    def test_2_binary_preview_still_uses_base64(self):
        data = b"\x89PNG\r\n\x1a\n" + b"x" * 40
        meta = protocol._file_meta("fid", "pic.png", "image/png", data)
        self.assertEqual(meta["preview"],
                         base64.b64encode(data[:512]).decode("ascii"))

    def test_3_chinese_truncates_by_character(self):
        preview = protocol._text_preview(("中文" * 400).encode("utf-8"), limit=20)
        self.assertEqual(len(preview), 20)
        self.assertNotIn("\ufffd", preview)


class SendTxtTests(unittest.TestCase):
    def _file(self, name="拖出验证.txt", content=CONTENT):
        p = TMP / name
        p.write_bytes(content.encode("utf-8"))
        return str(p)

    def test_1_prepare_204_means_delivered_for_txt(self):
        with mock.patch.object(protocol, "prepare",
                               return_value=(True, "", {}, "", 204)):
            results = protocol.send_files({}, {}, [self._file()])
        self.assertEqual(len(results), 1)
        name, res = results[0]
        self.assertTrue(name.endswith(".txt"))
        self.assertTrue(res.ok)
        self.assertTrue(res.no_transfer)

    def test_2_prepare_gets_plaintext_preview_and_text_type(self):
        seen = {}

        def fake_prepare(_peer, _info, metas, **_kw):
            seen.update(metas)
            fid = next(iter(metas))
            return True, "sid", {fid: "token"}, "", 200

        with mock.patch.object(protocol, "prepare", side_effect=fake_prepare), \
             mock.patch.object(protocol, "_upload_one",
                               return_value=protocol.SendResult(True)) as up:
            results = protocol.send_files({}, {}, [self._file()])
        self.assertTrue(results[0][1].ok)
        meta = next(iter(seen.values()))
        self.assertEqual(meta["fileType"], "text")
        self.assertEqual(meta["preview"], CONTENT)
        self.assertEqual(up.call_args.args[-1], CONTENT.encode("utf-8"))

    def test_3_binary_204_is_still_failure(self):
        blob = TMP / "data.bin"
        blob.write_bytes(b"\x00\x01\x02")
        with mock.patch.object(protocol, "prepare",
                               return_value=(True, "", {}, "", 204)):
            results = protocol.send_files({}, {}, [str(blob)])
        self.assertFalse(results[0][1].ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
