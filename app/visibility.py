"""设备可见性的唯一判断入口（v2.5.1）。

删除和屏蔽是两件事：
  - 删除：只移出会话列表，不写任何持久化名单，下次发现会重新出现；
  - 屏蔽：写进 blocked 名单，发现结果一律过滤，但必须能在「已屏蔽」里恢复。

刷新设备与手动添加共用 split_visible()；手动输入 IP 是明确意图，
由 force_visible() 先把该指纹从屏蔽名单里拿掉，再进入同一套判断。
"""
from __future__ import annotations


def force_visible(fp: str, blocked: set[str] | None) -> set[str]:
    """手动添加设备：强制绕过屏蔽，返回新的屏蔽集合。"""
    result = set(blocked or ())
    result.discard((fp or "").strip())
    return result


def split_visible(online: dict | None = None,
                  peers: list | None = None,
                  blocked: set[str] | None = None,
                  my_fp: str = "") -> tuple[list, list]:
    """把发现结果拆成 (在线可见, 离线可见)。

    同一指纹只出现一次：在线优先，离线表里重复的不再出现。
    顶栏计数必须基于这里返回的在线列表，不能读过滤前的原始发现结果。
    """
    deny = {x for x in (blocked or ()) if x}
    mine = (my_fp or "").strip()
    seen: set[str] = set()
    on_list: list[tuple[str, dict]] = []
    off_list: list[tuple[str, dict]] = []

    for fp, info in (online or {}).items():
        fp = (fp or "").strip()
        if not fp or fp == mine or fp in deny or fp in seen:
            continue
        seen.add(fp)
        on_list.append((fp, dict(info or {})))

    for p in peers or []:
        row = dict(p or {})
        fp = (row.get("fingerprint") or "").strip()
        if not fp or fp == mine or fp in deny or fp in seen:
            continue
        seen.add(fp)
        off_list.append((fp, row))

    return on_list, off_list


def count_online(rows: list | None) -> int:
    """列表里真正数得出来的在线数量，用于顶栏与状态栏。"""
    return sum(1 for r in (rows or []) if isinstance(r, dict) and r.get("online"))
