"""自签名证书管理：零第三方依赖，纯标准库生成与解析。"""
from __future__ import annotations

import base64
import hashlib
import socket

from . import purecert
from .config import CERT_DIR, local_ip

CERT_PEM = CERT_DIR / "cert.pem"
KEY_PEM = CERT_DIR / "key.pem"
FINGERPRINT_FILE = CERT_DIR / "fingerprint.txt"


def _san_list() -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = [("DNS", "localhost"), ("IP", "127.0.0.1")]
    try:
        host = socket.gethostname()
        if host and all(host != v for _k, v in items):
            items.append(("DNS", host))
    except Exception:
        pass
    try:
        ip = local_ip()
        if ip and ip != "127.0.0.1" and all(ip != v for _k, v in items):
            items.append(("IP", ip))
    except Exception:
        pass
    return items


def generate(force: bool = False) -> None:
    """生成 10 年期自签名证书；已存在则跳过。"""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    if CERT_PEM.exists() and KEY_PEM.exists() and not force:
        return

    der, key_pem = purecert.make_self_signed(
        common_name="LocalSend Chat",
        org="LocalSend Chat",
        sans=_san_list(),
        days=3650,
    )
    KEY_PEM.write_text(key_pem, encoding="ascii")
    CERT_PEM.write_text(purecert.pem_cert(der), encoding="ascii")
    FINGERPRINT_FILE.write_text(
        _colon(hashlib.sha256(der).hexdigest()).upper(), encoding="utf-8"
    )


def _colon(hexstr: str) -> str:
    return ":".join(hexstr[i : i + 2] for i in range(0, len(hexstr), 2))


def _der() -> bytes:
    """从 PEM 还原 DER，用于计算指纹。"""
    body: list[str] = []
    for line in CERT_PEM.read_text(encoding="ascii").splitlines():
        text = line.strip()
        if not text or text.startswith("-----"):
            continue
        body.append(text)
    return base64.b64decode("".join(body))


def fingerprint_forms() -> tuple[str, str, str]:
    """返回 (大写冒号式, 小写冒号式, 纯 hex 小写)。

    LocalSend 官方客户端用「证书 DER 的 SHA-256、冒号分隔」作为指纹，但不同版本
    对大小写处理不一致，因此同时给出几种形态，比对时一律归一化。
    """
    plain = hashlib.sha256(_der()).hexdigest()
    colon = _colon(plain)
    return colon.upper(), colon, plain


def fingerprint(style: str = "upper") -> str:
    upper, lower, _ = fingerprint_forms()
    return upper if style != "lower" else lower


def normalize_fp(value: str | None) -> str:
    """把任意写法的指纹归一成纯小写 hex。"""
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isalnum()).lower()


def ensure(force: bool = False) -> str:
    generate(force=force)
    return fingerprint()


def summary() -> dict:
    upper, lower, plain = fingerprint_forms()
    return {"path": str(CERT_PEM), "upper": upper, "lower": lower, "plain": plain}
