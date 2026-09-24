# -*- coding: utf-8 -*-
"""极简 YAML 输出器（只写不读，标准库实现）。

只覆盖 JSON 能表达的数据（dict / list / str / int / float / bool / None），输出块风格 YAML。
字符串是否加引号按「YAML 1.1 解析器也不会误读」的保守规则判定：Java 侧常用的 SnakeYAML
是 1.1 语义，会把 yes / on / 2026-01-01 / 0x1F 这类裸值读成布尔、日期、数字，所以一律加引号。
"""

import math
import re

# 1.1 / 1.2 里会被解析成非字符串的裸词（比较时忽略大小写）
_RESERVED = {
    "true", "false", "yes", "no", "on", "off", "y", "n",
    "null", "~", ".nan", ".inf", "-.inf", "+.inf", "<<", "=",
}
# 允许裸写的字符串：以字母、下划线、斜杠或非 ASCII 字符开头，其余字符里不含 YAML 指示符
_PLAIN = re.compile(r"^[A-Za-z_/ -￿][^#,\[\]{}&*!|>'\"%@`\x00-\x1f\x7f-\x9f]*$")
_UNSAFE_CHARS = re.compile("[  ﻿]")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}


def _is_plain(text):
    if not _PLAIN.match(text) or _UNSAFE_CHARS.search(text):
        return False
    if text != text.strip() or text.endswith(":"):
        return False
    if ": " in text or " #" in text:
        return False
    return text.lower() not in _RESERVED


def _quote(text):
    """双引号字符串：只转义必须转义的字符，中文原样保留"""
    out = []
    for ch in text:
        code = ord(ch)
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif code < 0x20 or 0x7f <= code <= 0x9f or ch in "  ﻿":
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
    return '"%s"' % "".join(out)


def _block_literal(text, indent):
    """多行说明文字用 `|` 块，便于人读；首行以空格开头等边界情况退回双引号"""
    if "\r" in text or text.startswith((" ", "\n")) or text.endswith("\n\n"):
        return None
    if _UNSAFE_CHARS.search(text) or any(ord(c) < 0x20 and c not in "\n\t" for c in text):
        return None
    lines = text.rstrip("\n").split("\n")
    if any(line != line.rstrip() for line in lines):
        return None                       # 行尾空白在块里不直观，退回引号写法
    head = "|" if text.endswith("\n") else "|-"
    pad = " " * indent
    return head + "\n" + "\n".join((pad + line) if line else "" for line in lines)


def _scalar(value, indent=0, allow_block=False):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ".nan"
        if math.isinf(value):
            return ".inf" if value > 0 else "-.inf"
        text = repr(value)
        # YAML 1.1 的浮点正则要求有小数点：1e+20 会被读成字符串，写成 1.0e+20
        if "e" in text and "." not in text:
            mantissa, exponent = text.split("e")
            text = "%s.0e%s" % (mantissa, exponent)
        return text
    if isinstance(value, str):
        if allow_block and "\n" in value:
            block = _block_literal(value, indent)
            if block is not None:
                return block
        return value if _is_plain(value) else _quote(value)
    raise TypeError("不支持输出为 YAML 的类型：%r" % type(value))


def _key(key):
    if not isinstance(key, str):
        raise TypeError("YAML 映射键必须是字符串：%r" % (key,))
    return key if _is_plain(key) else _quote(key)


def _emit(value, indent, lines):
    """把容器 value 写成若干行（value 必须是非空 dict / list）"""
    pad = " " * indent
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                lines.append("%s%s:" % (pad, _key(k)))
                _emit(v, indent + 2, lines)
            else:
                lines.append("%s%s: %s" % (pad, _key(k), _inline(v, indent + 2)))
    else:
        for item in value:
            if isinstance(item, dict) and item:
                # 列表里的映射：首个键与 "- " 同行，其余键对齐到它
                sub = []
                _emit(item, indent + 2, sub)
                sub[0] = pad + "- " + sub[0][indent + 2:]
                lines.extend(sub)
            elif isinstance(item, list) and item:
                lines.append(pad + "-")
                _emit(item, indent + 2, lines)
            else:
                lines.append("%s- %s" % (pad, _inline(item, indent + 2)))


def _inline(value, indent):
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, list):
        return "[]"
    return _scalar(value, indent, allow_block=True)


def dump(value):
    """把 JSON 兼容的数据写成 YAML 文本（以换行结尾）"""
    if isinstance(value, (dict, list)) and value:
        lines = []
        _emit(value, 0, lines)
        return "\n".join(lines) + "\n"
    return _inline(value, 0) + "\n"
