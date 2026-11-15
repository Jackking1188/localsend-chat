# -*- coding: utf-8 -*-
"""v2.5.1 回归测试：删除 / 屏蔽分离、计数一致、发现去重。

运行方式（仓库根目录）：
    python -m unittest discover -s tests -v

数据一律写到临时 LSC_HOME，绝不触碰真实的聊天记录与配置。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="lsc-v251-test-")
os.environ["LSC_HOME"] = _TMP                      # 必须在导入 app.* 之前
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import discovery, ui                       # noqa: E402
from app.storage import Store                       # noqa: E402
from app.visibility import (                        # noqa: E402
    count_online, force_visible, split_visible,
)

MINE = "MINE-FINGERPRINT"


def peer(fp: str, alias: str, ip: str = "192.168.1.50") -> dict:
    return {
        "alias": alias, "fingerprint": fp, "ip": ip, "port": 53317,
        "protocol": "https", "deviceType": "mobile", "version": "2.1",
        "deviceModel": "Phone",
    }


def dbrow(fp: str, alias: str, ip: str = "192.168.1.50") -> dict:
    return {"fingerprint": fp, "alias": alias, "ip": ip, "port": 53317,
            "protocol": "https", "device": "Phone", "last_seen": 0.0}


# ------------------------------------------------------------------
class VisibilityTests(unittest.TestCase):
    """刷新与手动添加共用的唯一判断入口。"""

    def test_only_blocked_and_self_are_filtered(self):
        online = {"A": peer("A", "甲"), "B": peer("B", "乙"),
                  MINE: peer(MINE, "我")}
        on, off = split_visible(online, [dbrow("A", "甲"),
                                         dbrow("C", "丙")],
                                {"B"}, MINE)
        self.assertEqual([x[0] for x in on], ["A"])
        self.assertEqual([x[0] for x in off], ["C"])

    def test_fingerprint_dedup_online_wins(self):
        on, off = split_visible({"A": peer("A", "在线")},
                                [dbrow("A", "离线"), dbrow("A", "又一条")],
                                set(), MINE)
        self.assertEqual(len(on), 1)
        self.assertEqual(off, [])

    def test_count_equals_visible_rows(self):
        on, off = split_visible(
            {"A": peer("A", "甲"), "B": peer("B", "乙")},
            [dbrow("C", "丙")], {"B"}, MINE)
        rows = ([{"online": True} for _ in on] +
                [{"online": False} for _ in off])
        self.assertEqual(count_online(rows), len(on))

    def test_manual_add_bypasses_blocklist(self):
        self.assertEqual(force_visible("A", {"A", "B"}), {"B"})
        self.assertEqual(force_visible("A", set()), set())


# ------------------------------------------------------------------
class StorageTests(unittest.TestCase):
    """删除绝不写持久化名单；只有屏蔽写。"""

    def setUp(self):
        self.db = Path(_TMP) / ("store_%s.db" % self._testMethodName)
        self.store = Store(self.db)
        self.fp = "FP-PHONE"
        self.store.upsert_peer(peer(self.fp, "手机"), "192.168.1.50")
        self.store.add_message("in", self.fp, "手机", "你好", ts=1.0)

    def tearDown(self):
        self.store.close()

    def test_delete_never_writes_blocklist(self):
        self.store.delete_peer(self.fp, drop_history=False)
        self.assertEqual(self.store.blocked_fingerprints(), set())
        self.assertEqual(self.store.hidden_peers(), [])
        self.assertEqual(len(self.store.peers()), 0)
        # 选「否」时聊天记录保留
        self.assertEqual(len(self.store.history(self.fp)), 1)

    def test_delete_can_drop_history_explicitly(self):
        self.store.delete_peer(self.fp, drop_history=True)
        self.assertEqual(len(self.store.history(self.fp)), 0)

    def test_block_unblock_clear(self):
        self.store.block_peer(self.fp, "手机")
        self.assertEqual(self.store.blocked_fingerprints(), {self.fp})
        self.assertEqual(len(self.store.blocked_peers()), 1)
        self.store.unblock_peer(self.fp)
        self.assertEqual(self.store.blocked_fingerprints(), set())
        self.store.block_peer("FP-2", "邻居")
        self.store.block_peer("FP-3", "电视")
        self.store.clear_blocked()
        self.assertEqual(self.store.blocked_fingerprints(), set())


# ------------------------------------------------------------------
class DiscoveryDedupTests(unittest.TestCase):
    """同一指纹在多个 IP / 组播+扫描各命中一次，只算一台。"""

    def test_found_counts_unique_fingerprints(self):
        mapping = {
            "192.168.77.21": "FP-A",   # 同一台占了 3 个 IP
            "192.168.77.22": "FP-A",
            "192.168.77.23": "FP-A",
            "192.168.77.24": "FP-B",
            "192.168.77.25": "FP-B",
        }

        class FakeResp:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self.data

        class FakeOpener:
            def open(self, req, timeout=None):
                host = req.full_url.split("//")[1].split(":")[0]
                fp = mapping.get(host)
                if not fp:
                    raise OSError("offline")
                return FakeResp(json.dumps(
                    {"alias": "dev-" + fp, "fingerprint": fp}).encode())

        seen: list[dict] = []
        d = discovery.Discoverer(lambda: {"fingerprint": MINE}, alias="me")
        d._on_peer = seen.append
        with mock.patch.object(discovery.Discoverer, "_local_ips",
                               staticmethod(lambda: ["192.168.77.10"])), \
             mock.patch.object(discovery.urllib.request, "build_opener",
                               lambda *a, **k: FakeOpener()):
            found = d.scan_subnet()
        self.assertEqual(found, 2)                 # 不是 5
        self.assertEqual(d.stats.scan_hit, 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual({x["fingerprint"] for x in seen}, {"FP-A", "FP-B"})


# ------------------------------------------------------------------
class _FakeServer:
    port = 53317

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class _FakeShare:
    def stop(self):
        pass


class _FakeDiscoverer:
    def __init__(self, my_info, alias, port=53317):
        self.alias = alias
        self.port = port
        self.stats = types.SimpleNamespace(as_dict=lambda: {})

    def start(self, on_peer):
        self.on_peer = on_peer

    def stop(self):
        pass

    def scan_subnet(self, progress=None):
        return 0


class AppRegressionTests(unittest.TestCase):
    """真实 Tk 界面 + 真实删除/屏蔽/计数逻辑，网络层用替身。"""

    @classmethod
    def setUpClass(cls):
        cls._patches = [
            mock.patch.object(ui, "Server", _FakeServer),
            mock.patch.object(ui.share, "ShareServer", lambda: _FakeShare()),
            mock.patch.object(ui, "Discoverer", _FakeDiscoverer),
            mock.patch.object(ui.certs, "ensure", lambda: None),
            mock.patch.object(ui.certs, "fingerprint", lambda: MINE),
            mock.patch.object(ui.App, "_tick", lambda self: None),
        ]
        for p in cls._patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls._patches):
            p.stop()

    def setUp(self):
        db = Path(_TMP) / "chat.db"
        try:
            db.unlink()
        except OSError:
            pass
        self.app = ui.App()
        self.app.withdraw()
        self.fp = "FP-PHONE"
        self.info = peer(self.fp, "真我 GT7 Pro")
        self.app.store.upsert_peer(self.info, self.info["ip"])
        self.app.online[self.fp] = self.info
        self.app._reload_keep_selection()

    def tearDown(self):
        try:
            self.app._on_close()
        except Exception:
            try:
                self.app.destroy()
            except Exception:
                pass

    def _fps(self):
        return [r["fp"] for _k, r in self.app._rows]

    def _row(self):
        return next(r for _k, r in self.app._rows if r["fp"] == self.fp)

    def _delete(self):
        with mock.patch.object(ui.messagebox, "askyesnocancel",
                               return_value=False):
            self.app._delete_peer(self._row())

    # 用例 1
    def test_1_delete_then_refresh_device_returns(self):
        self.assertIn(self.fp, self._fps())
        self._delete()
        self.assertEqual(self.app.store.blocked_fingerprints(), set())
        self.assertNotIn(self.fp, self.app.online)
        self.app._handle_peer({"peer": self.info})      # 模拟刷新命中
        self.assertIn(self.fp, self._fps())

    # 用例 2
    def test_2_delete_then_manual_add_succeeds(self):
        self._delete()
        self.app._handle_manual_probe({"ip": self.info["ip"],
                                       "info": self.info})
        self.assertIn(self.fp, self._fps())
        self.assertNotIn(self.fp, self.app._blocked)

    def test_2b_manual_add_bypasses_blocklist(self):
        with mock.patch.object(ui.messagebox, "askyesno", return_value=True):
            self.app._block_peer(self._row())
        self.assertIn(self.fp, self.app.store.blocked_fingerprints())
        self.assertNotIn(self.fp, self._fps())
        self.app._handle_manual_probe({"ip": self.info["ip"],
                                       "info": self.info})
        self.assertIn(self.fp, self._fps())
        self.assertEqual(self.app.store.blocked_fingerprints(), set())

    # 用例 3
    def test_3_block_hides_but_can_be_restored(self):
        with mock.patch.object(ui.messagebox, "askyesno", return_value=True):
            self.app._block_peer(self._row())
        self.assertEqual(self.app.lbl_blocked.cget("text"), "已屏蔽 (1)")
        self.app._handle_peer({"peer": self.info})      # 刷新也回不来
        self.assertNotIn(self.fp, self._fps())
        self.app._unblock_peer(self.fp)
        self.app._handle_peer({"peer": self.info})
        self.assertIn(self.fp, self._fps())
        self.assertEqual(self.app.lbl_blocked.cget("text"), "已屏蔽 (0)")

    # 用例 4
    def test_4_header_count_equals_visible_rows(self):
        for fp, alias in (("FP-B", "平板"), ("FP-C", "电视"),
                          ("FP-D", "邻居")):
            self.app.store.upsert_peer(peer(fp, alias), "192.168.1.60")
        self.app.online["FP-B"] = peer("FP-B", "平板", "192.168.1.61")
        self.app.online["FP-C"] = peer("FP-C", "电视", "192.168.1.62")
        self.app._blocked = {"FP-D"}
        self.app.store.block_peer("FP-D", "邻居")
        self.app._reload_keep_selection()

        visible_online = sum(1 for _k, r in self.app._rows if r["online"])
        self.assertEqual(self.app.lbl_count.cget("text"),
                         f"{visible_online} 在线")
        self.assertEqual(visible_online, count_online(
            [r for _k, r in self.app._rows]))
        # 故意传一个错的原始 online=99，状态栏仍应显示过滤后的数字
        self.app._handle_scan_done({"found": 3, "online": 99, "stats": {}})
        self.assertIn(f"在线 {visible_online} 台",
                      self.app.lbl_status.cget("text"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
