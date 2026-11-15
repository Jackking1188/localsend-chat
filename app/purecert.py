"""纯 Python 自签名证书生成：零第三方依赖。

Windows 上 pip 安装 cryptography 常因 Python 版本过新（无预编译轮子）、缺少
MSVC/Rust 工具链或网络受限而失败。为了让「双击安装」真正全自动，这里用标准库
实现了完整的证书链路：

  * Miller-Rabin 素性检测 + secrets 随机数，生成 2048 位 RSA 密钥对
  * 手写 DER/ASN.1 编码，构造 X.509 v3 TBSCertificate
  * RSASSA-PKCS1-v1_5 + SHA-256 签名
  * 输出 PEM（PKCS#1 私钥 / X.509 证书），Python ssl 可直接加载

首次生成约 1~3 秒，之后复用磁盘上的证书不再重算。
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import secrets
import time

_E = 65537
_MR_ROUNDS = 24


# ---------------------------------------------------------------- 素数与密钥
def _small_primes(limit: int = 2000) -> list[int]:
    sieve = bytearray([1]) * limit
    sieve[0:2] = b"\x00\x00"
    for i in range(2, int(limit ** 0.5) + 1):
        if sieve[i]:
            sieve[i * i :: i] = bytearray(len(sieve[i * i :: i]))
    return [i for i in range(limit) if sieve[i]]


_SMALL = _small_primes()


def _is_probable_prime(n: int) -> bool:
    if n < 2:
        return False
    for p in _SMALL:
        if n % p == 0:
            return n == p
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(_MR_ROUNDS):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int) -> int:
    while True:
        cand = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        # 保证与 e=65537 互素：p ≡ 1 (mod e) 时 phi 会与 e 不互素
        if cand % _E == 1:
            continue
        if _is_probable_prime(cand):
            return cand


def generate_key(bits: int = 2048) -> dict:
    half = bits // 2
    while True:
        p = _gen_prime(half)
        q = _gen_prime(half)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        phi = (p - 1) * (q - 1)
        if phi % _E == 0:
            continue
        d = pow(_E, -1, phi)
        return {
            "n": n, "e": _E, "d": d, "p": p, "q": q,
            "dp": d % (p - 1), "dq": d % (q - 1), "qinv": pow(q, -1, p),
            "bytes": (n.bit_length() + 7) // 8,
        }


# ---------------------------------------------------------------- DER/ASN.1
def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + _len(len(payload)) + payload


def _int(v: int) -> bytes:
    if v == 0:
        return _tlv(0x02, b"\x00")
    body = v.to_bytes((v.bit_length() + 7) // 8, "big")
    if body[0] & 0x80:
        body = b"\x00" + body
    return _tlv(0x02, body)


def _seq(*items: bytes) -> bytes:
    return _tlv(0x30, b"".join(items))


def _set(*items: bytes) -> bytes:
    return _tlv(0x31, b"".join(items))


def _oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    body = [40 * parts[0] + parts[1]] + parts[2:]
    out = bytearray()
    for v in body:
        chunk = [v & 0x7F]
        v >>= 7
        while v:
            chunk.append((v & 0x7F) | 0x80)
            v >>= 7
        out += bytes(reversed(chunk))
    return _tlv(0x06, bytes(out))


def _null() -> bytes:
    return b"\x05\x00"


def _utf8(s: str) -> bytes:
    return _tlv(0x0C, s.encode("utf-8"))


def _octets(b: bytes) -> bytes:
    return _tlv(0x04, b)


def _bits(b: bytes, unused: int = 0) -> bytes:
    return _tlv(0x03, bytes([unused]) + b)


def _boolean(v: bool) -> bytes:
    return _tlv(0x01, b"\xff" if v else b"\x00")


def _utctime(ts: float) -> bytes:
    return _tlv(0x17, time.strftime("%y%m%d%H%M%SZ", time.gmtime(ts)).encode())


def _name(cn: str, org: str) -> bytes:
    # SEQUENCE OF RDN；每个 RDN = SET OF AttributeTypeAndValue
    cn_rdn = _set(_seq(_oid("2.5.4.3"), _utf8(cn)))
    org_rdn = _set(_seq(_oid("2.5.4.10"), _utf8(org)))
    return _seq(org_rdn, cn_rdn)


def _general_name(kind: str, value: str) -> bytes:
    if kind.upper() == "IP":
        return _tlv(0x87, ipaddress.ip_address(value).packed)   # [7] iPAddress
    return _tlv(0x82, value.encode("ascii"))                    # [2] dNSName


def _ext(oid: str, payload: bytes, critical: bool = False) -> bytes:
    parts = [_oid(oid)]
    if critical:
        parts.append(_boolean(True))
    parts.append(_octets(payload))
    return _seq(*parts)


# ---------------------------------------------------------------- 签名
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _sign_sha256(tbs: bytes, key: dict) -> bytes:
    k = key["bytes"]
    digest_info = _SHA256_DIGEST_INFO + hashlib.sha256(tbs).digest()
    pad_len = k - len(digest_info) - 3
    if pad_len < 8:
        raise ValueError("密钥长度不足")
    em = b"\x00\x01" + b"\xff" * pad_len + b"\x00" + digest_info
    m = int.from_bytes(em, "big")
    s = pow(m, key["d"], key["n"])
    return s.to_bytes(k, "big")


# ---------------------------------------------------------------- 组装
def make_self_signed(
    common_name: str = "LocalSend Chat",
    org: str = "LocalSend Chat",
    sans: list[tuple[str, str]] | None = None,
    days: int = 3650,
    key_bits: int = 2048,
) -> tuple[bytes, str]:
    """返回 (证书 DER 字节, 私钥 PEM 文本)。"""
    key = generate_key(key_bits)
    now = time.time()
    nb = now - 86400
    na = now + days * 86400

    serial = int.from_bytes(secrets.token_bytes(16), "big")
    serial = (serial | (1 << 127)) & ((1 << 128) - 1)   # 保证为正且足够大

    alg_sha256_rsa = _seq(_oid("1.2.840.113549.1.1.11"), _null())
    alg_rsa = _seq(_oid("1.2.840.113549.1.1.1"), _null())
    subject = _name(common_name, org)

    spki = _seq(
        alg_rsa,
        _bits(_seq(_int(key["n"]), _int(key["e"]))),
    )

    extensions = [
        _ext("2.5.29.19", _seq(_boolean(True)), critical=True),      # basicConstraints CA
        _ext("2.5.29.15", _bits(b"\xa0", 5), critical=True),         # keyUsage
    ]
    if sans:
        names = b"".join(_general_name(k, v) for k, v in sans)
        extensions.append(_ext("2.5.29.17", _seq(names)))            # subjectAltName

    tbs = _seq(
        _tlv(0xA0, _int(2)),                                          # [0] version v3
        _int(serial),
        alg_sha256_rsa,
        subject,
        _seq(_utctime(nb), _utctime(na)),
        subject,
        spki,
        _tlv(0xA3, _seq(*extensions)),                                # [3] extensions
    )

    sig = _sign_sha256(tbs, key)
    der = _seq(tbs, alg_sha256_rsa, _bits(sig))

    priv = _seq(
        _int(0),
        _int(key["n"]), _int(key["e"]), _int(key["d"]),
        _int(key["p"]), _int(key["q"]),
        _int(key["dp"]), _int(key["dq"]), _int(key["qinv"]),
    )
    key_pem = _pem("RSA PRIVATE KEY", priv)
    return der, key_pem


def _pem(label: str, der: bytes) -> str:
    body = base64.b64encode(der).decode("ascii")
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


def pem_cert(der: bytes) -> str:
    return _pem("CERTIFICATE", der)
