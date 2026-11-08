# -*- coding: utf-8 -*-
"""纯 Python 实现的 Ed25519 验签（RFC 8032）。

只做「验签」，不涉及签名。用标准库的 hashlib 做 SHA-512，不依赖
cryptography 的 Rust 后端，从而彻底避开 PyInstaller 打包时 pyo3 / 动态库
缺失或非 UTF-8 路径导致的崩溃问题。
"""

import hashlib

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)  # sqrt(-1)

# 基点 B
_B_X = 15112221349535400772501151409588531511454012693041857206046113283949847762202
_B_Y = 46316835694926478169428394003475163141307993866256225615783033603165251855960


def _xrecover(y):
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if (x * x - xx) % _P != 0:
        raise ValueError("not on curve")
    return x


def _add(P, Q):
    x1, y1 = P
    x2, y2 = Q
    x3 = (x1 * y2 + x2 * y1) * pow(1 + _D * x1 * x2 * y1 * y2, _P - 2, _P) % _P
    y3 = (y1 * y2 + x1 * x2) * pow(1 - _D * x1 * x2 * y1 * y2, _P - 2, _P) % _P
    return (x3, y3)


def _mul(n, P):
    Q = (0, 1)  # 单位元
    while n > 0:
        if n & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        n >>= 1
    return Q


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    if y >= _P:
        return None
    try:
        x = _xrecover(y)
    except (ValueError, ZeroDivisionError):
        return None
    if (x & 1) != sign:
        x = _P - x
    return (x, y)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """校验 Ed25519 签名。public_key/signature 均为 32/64 字节原始数据。"""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    r_enc = signature[:32]
    s_enc = signature[32:]
    s = int.from_bytes(s_enc, "little")
    if s >= _L:
        return False

    A = _decompress(public_key)
    R = _decompress(r_enc)
    if A is None or R is None:
        return False

    k = int.from_bytes(hashlib.sha512(r_enc + public_key + message).digest(), "little") % _L
    sB = _mul(s, (_B_X, _B_Y))
    rhs = _add(R, _mul(k, A))
    return sB == rhs
