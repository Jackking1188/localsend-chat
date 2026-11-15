"""设备发现：组播 + 广播 + 子网主动扫描三重策略。

v1 只做组播，一旦路由器过滤组播或本机入站被防火墙拦截就彻底失明。
v2 的取舍：

  * 发现端口固定 53317，与 HTTP 端口解耦（HTTP 可顺延，发现不行）；
  * 组播/广播包全部带自身 info，收到即记入统计，便于诊断；
  * **子网扫描**走主动出站连接，天然绕开「本机入站被拦」这个最常见故障；
  * 统计收发包数，UI 据此判断是「没发出去」还是「发出去了没人回」。
"""
from __future__ import annotations

import json
import socket
import ssl
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MULTICAST_ADDR = "224.0.0.167"
DISCOVERY_PORT = 53317

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


class Stats:
    """诊断计数器。"""

    __slots__ = ("sent", "recv", "joined", "join_fail", "scan_tried",
                 "scan_hit", "last_error", "bound_port")

    def __init__(self) -> None:
        self.sent = 0
        self.recv = 0
        self.joined = 0
        self.join_fail = 0
        self.scan_tried = 0
        self.scan_hit = 0
        self.last_error = ""
        self.bound_port = 0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


class Discoverer:
    def __init__(self, my_info_provider, alias: str,
                 port: int = DISCOVERY_PORT) -> None:
        self._my_info = my_info_provider
        self.alias = alias
        self.port = port
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._on_peer = None
        self._seen: dict[str, float] = {}
        self._lock = threading.RLock()
        self.stats = Stats()
        self._scanning = False

    # ------------------------------------------------------------ 生命周期
    def start(self, on_peer) -> None:
        self._on_peer = on_peer
        self._stop.clear()
        self._sock = self._make_socket()
        self._threads = [
            threading.Thread(target=self._listen, name="ls-listen", daemon=True),
            threading.Thread(target=self._announce, name="ls-announce", daemon=True),
        ]
        for t in self._threads:
            t.start()
        # 启动后自动做一轮扫描兜底
        threading.Thread(target=self.scan_subnet, name="ls-scan-boot",
                         daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    # ------------------------------------------------------------ socket
    def _make_socket(self) -> socket.socket | None:
        """必须绑定 53317：其他设备只会往这个端口发组播。"""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        for opt in ("SO_REUSEPORT",):
            if hasattr(socket, opt):
                try:
                    s.setsockopt(socket.SOL_SOCKET, getattr(socket, opt), 1)
                except Exception:
                    pass
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass

        bound = False
        try:
            s.bind(("", self.port))
            bound = True
            self.stats.bound_port = self.port
        except OSError as e:
            self.stats.last_error = f"绑定 UDP {self.port} 失败：{e}"
            try:
                s.bind(("", 0))
                self.stats.bound_port = s.getsockname()[1]
            except OSError:
                s.close()
                return None

        if bound:
            for iface in self._local_ips():
                try:
                    mreq = socket.inet_aton(MULTICAST_ADDR) + socket.inet_aton(iface)
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                    self.stats.joined += 1
                except OSError:
                    self.stats.join_fail += 1
            # 兜底：默认接口
            try:
                mreq = socket.inet_aton(MULTICAST_ADDR) + socket.inet_aton("0.0.0.0")
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                self.stats.joined += 1
            except OSError:
                self.stats.join_fail += 1

        s.settimeout(0.5)
        return s

    @staticmethod
    def _local_ips() -> list[str]:
        ips: list[str] = []
        try:
            host = socket.gethostname()
            for info in socket.getaddrinfo(host, None, socket.AF_INET):
                ip = info[4][0]
                if ip not in ips and not ip.startswith("127."):
                    ips.append(ip)
        except Exception:
            pass
        if not ips:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ips.append(s.getsockname()[0])
                s.close()
            except Exception:
                pass
        return ips

    # ------------------------------------------------------------ 宣告线程
    def _announce(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is not None:
                try:
                    payload = json.dumps(self._my_info(), ensure_ascii=False).encode("utf-8")
                except Exception:
                    payload = b"{}"
                targets = [(MULTICAST_ADDR, self.port), ("255.255.255.255", self.port)]
                for ip in self._local_ips():
                    parts = ip.split(".")
                    if len(parts) == 4:
                        targets.append((".".join(parts[:3]) + ".255", self.port))
                for target in targets:
                    try:
                        sock.sendto(payload, target)
                        self.stats.sent += 1
                    except OSError as e:
                        self.stats.last_error = f"发送失败：{e}"
            self._stop.wait(5.0)

    # ------------------------------------------------------------ 接收线程
    def _listen(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                time.sleep(0.2)
                continue
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            self.stats.recv += 1
            try:
                info = json.loads(data.decode("utf-8", "replace"))
            except Exception:
                continue
            if not isinstance(info, dict) or not info.get("alias"):
                continue

            fp = (info.get("fingerprint") or "").strip()
            mine = (self._my_info().get("fingerprint") or "").strip()
            if not fp or fp == mine:
                continue

            info["ip"] = addr[0]
            now = time.time()
            with self._lock:
                if now - self._seen.get(fp, 0.0) < 2.0:
                    continue
                self._seen[fp] = now

            # 回应，让对方也能发现我们
            try:
                reply = json.dumps(self._my_info(), ensure_ascii=False).encode("utf-8")
                sock.sendto(reply, addr)
                self.stats.sent += 1
            except OSError:
                pass

            self._emit(info)

    def _emit(self, info: dict) -> None:
        if self._on_peer is None:
            return
        try:
            self._on_peer(info)
        except Exception:
            pass

    # ------------------------------------------------------------ 主动扫描
    def scan_subnet(self, progress=None) -> int:
        """扫本机 /24 网段的 53317，返回发现数量。

        走主动出站 TCP，因此即使本机入站被防火墙拦住也能看到对方——
        这是组播失效时唯一可靠的兜底。
        """
        if self._scanning:
            return 0
        self._scanning = True
        found = 0
        try:
            ips = self._local_ips()
            if not ips:
                return 0
            base = ".".join(ips[0].split(".")[:3])
            hosts = [f"{base}.{i}" for i in range(1, 255)]
            my_ips = set(ips)
            hosts = [h for h in hosts if h not in my_ips]

            def probe(ip: str):
                try:
                    url = f"https://{ip}:{self.port}/api/localsend/v2/info"
                    req = urllib.request.Request(url, method="GET")
                    opener = urllib.request.build_opener(
                        urllib.request.HTTPSHandler(context=_SSL_CTX))
                    with opener.open(req, timeout=0.9) as resp:
                        data = json.loads(resp.read())
                    if isinstance(data, dict) and data.get("alias"):
                        data["ip"] = ip
                        return data
                except Exception:
                    return None
                return None

            self.stats.scan_tried += len(hosts)
            # 同一台设备可能占多个 IP（WLAN / 热点 / 虚拟网卡），
            # 按指纹去重后再累加，否则「本次新发现 N 台」会虚高。
            counted: set[str] = set()
            with ThreadPoolExecutor(max_workers=64) as pool:
                for info in pool.map(probe, hosts):
                    if info:
                        fp = (info.get("fingerprint") or "").strip()
                        mine = (self._my_info().get("fingerprint") or "").strip()
                        if fp and fp != mine:
                            with self._lock:
                                self._seen[fp] = time.time()
                            if fp in counted:
                                continue
                            counted.add(fp)
                            found += 1
                            self.stats.scan_hit += 1
                            self._emit(info)
                            if progress:
                                try:
                                    progress(info)
                                except Exception:
                                    pass
        finally:
            self._scanning = False
        return found

    @property
    def scanning(self) -> bool:
        return self._scanning


def probe_ip(ip: str, port: int = DISCOVERY_PORT, timeout: float = 3.0) -> dict | None:
    """直连指定 IP 拉取设备信息，用于「手动添加设备」。"""
    ip = ip.strip()
    if ip.startswith("http"):
        # 允许用户直接贴 URL
        url = ip.rstrip("/") + "/api/localsend/v2/info"
        host_part = ip.split("//", 1)[-1].split(":")[0].split("/")[0]
    else:
        host_part = ip
        url = f"https://{ip}:{port}/api/localsend/v2/info"
    try:
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_SSL_CTX))
        with opener.open(urllib.request.Request(url, method="GET"),
                         timeout=timeout) as resp:
            data = json.loads(resp.read())
        if isinstance(data, dict) and data.get("alias"):
            data["ip"] = host_part
            return data
    except Exception:
        return None
    return None
