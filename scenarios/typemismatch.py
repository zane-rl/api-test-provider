# -*- coding: utf-8 -*-
"""F 类：文档声明的字段类型与接口实际返回**故意不一致**的场景。

⚠️ **全项目唯一一个「文档与实现不一致」的地方，是刻意的，不要当 bug 修。**
别的场景的 OpenAPI 都由「真的调一次处理函数」推断出来，天然等于实现；
这个场景反过来——显式手写 schema 覆盖推断结果，让文档声明与真实响应对不上。

用途：后端「验证接口」时会把真实响应与已保存的字段类型做兼容性比对，
不兼容就在验证阶段报告，而不是等首次同步才失败。这里给它一个能稳定触发的对端。

每个端点都在 `/__admin/scenarios` 里带 `declaredVsActual`，E2E 直接取它当期望：
`[{field, declaredType, declaredFormat, actualJsonType, expectIncompatible}]`。
注意有两个**负例**（`int-vs-decimal`、`nullable-mostly-null`）：类型写法不同但后端不该判不兼容。
"""

import json
from datetime import timedelta

from .common import (
    Endpoint, Scenario, NO_AUTH, BASE_TIME, iso, json_resp, json_type_of,
)

COUNT = 6
COLORS = ["red", "green", "blue"]


# ==================== 造数（固定公式） ====================

def _rows(prefix, extra):
    """每条记录 = id + name + 该端点要演示的字段"""
    out = []
    for i in range(1, COUNT + 1):
        row = {"id": "%s-%05d" % (prefix, i), "name": "类型样本%d" % i}
        row.update(extra(i))
        out.append(row)
    return out


def _epoch_ms(i):
    return int((BASE_TIME + timedelta(hours=i)).timestamp() * 1000)


NUMBER_AS_STRING = _rows("TMNUM", lambda i: {"quantity": i * 12})
STRING_AS_NUMBER = _rows("TMSTR", lambda i: {"amount": "%.2f" % (12.5 * i)})
# 布尔字符串的四种常见写法都要出现：下游按 BOOLEAN 读时 "1"/"0" 与 "true"/"false" 一样会炸，
# 而「把字符串转布尔」的兼容逻辑常常只认其中一种写法
BOOL_STRING_VALUES = ["true", "false", "1", "0", "true", "false"]
BOOL_AS_STRING = _rows("TMBOOL", lambda i: {"active": BOOL_STRING_VALUES[i - 1]})
# 对照组：同一份文档声明（active: boolean），实际也返回真布尔——下游应当正常
BOOL_AS_BOOL = _rows("TMBOOLOK", lambda i: {"active": i % 2 == 1})
DATETIME_AS_EPOCH = _rows("TMDATE", lambda i: {"updated_at": _epoch_ms(i)})
OBJECT_AS_STRING = _rows("TMOBJ", lambda i: {
    "extra": json.dumps({"color": COLORS[i % len(COLORS)], "size": i}, ensure_ascii=False)})
INT_VS_DECIMAL = _rows("TMDEC", lambda i: {"price": round(12.5 * i, 2)})
NULLABLE_MOSTLY_NULL = _rows("TMNULL", lambda i: {
    # 6 条里只有第 3 条不是 null：用来验证「样本里大多为 null」不该被判成类型不兼容
    "remark": "仅此一条有值" if i == 3 else None})
CONSISTENT = _rows("TMOK", lambda i: {
    "seq": i,
    "price": round(12.5 * i, 2),
    "active": i % 2 == 1,
    "updated_at": iso(BASE_TIME + timedelta(hours=i)),
    "remark": None if i % 3 == 0 else "备注%d" % i,
})


# ==================== 声明用的 schema（手写，故意与实现不一致） ====================

def _envelope(record_properties):
    return {
        "type": "object",
        "properties": {
            "code": {"type": "integer"},
            "message": {"type": "string"},
            "data": {"type": "object", "properties": {
                "items": {"type": "array", "items": {
                    "type": "object", "properties": record_properties}}}},
        },
    }


_ID_NAME = {"id": {"type": "string"}, "name": {"type": "string"}}


def _declared(**fields):
    props = dict(_ID_NAME)
    props.update(fields)
    return props


# ==================== 端点 ====================

def _endpoint(path, operation_id, summary, rows, declared_properties, comparisons,
              description):
    def handler(ctx):
        return json_resp({"code": 0, "message": "ok", "data": {"items": rows}})

    # 自检：declaredVsActual 里写的「实际类型」必须真的等于响应里的类型，
    # 否则这张给 E2E 用的期望表本身就是错的
    for item in comparisons:
        actual = json_type_of(rows[0][item["field"]])
        if item["field"] == "remark":
            actual = json_type_of(next(r["remark"] for r in rows if r["remark"] is not None))
        assert actual == item["actualJsonType"], (path, item["field"], actual)

    return Endpoint(
        "GET", path, handler,
        operation_id=operation_id, summary=summary, description=description,
        record_path="data.items", primary_key="id",
        dataset=lambda: list(rows),
        # 显式 schema：绕开「由真实响应推断」，这正是本场景要的不一致
        response_schema=_envelope(declared_properties),
        doc_sample=None,
        extra_catalog={"declaredVsActual": comparisons},
        note="文档声明与实际返回%s" % ("一致（对照组）" if all(
            not c["expectIncompatible"] for c in comparisons) else "故意不一致"),
    )


def _cmp(field, declared_type, actual_type, expect_incompatible,
         declared_format=None, note=None):
    item = {"field": field, "declaredType": declared_type,
            "declaredFormat": declared_format, "actualJsonType": actual_type,
            "expectIncompatible": expect_incompatible}
    if note:
        item["note"] = note
    return item


def build_scenarios():
    endpoints = [
        _endpoint(
            "/number-as-string", "listNumberAsString", "查询数据（数值字段被声明为字符串）",
            NUMBER_AS_STRING, _declared(quantity={"type": "string"}),
            [_cmp("quantity", "string", "integer", True)],
            "文档声明 quantity 为 string，实际返回 JSON 数值（如 12）"),
        _endpoint(
            "/string-as-number", "listStringAsNumber", "查询数据（字符串字段被声明为数值）",
            STRING_AS_NUMBER, _declared(amount={"type": "number"}),
            [_cmp("amount", "number", "string", True)],
            "文档声明 amount 为 number，实际返回字符串（如 「12.50」）"),
        _endpoint(
            "/bool-as-string", "listBoolAsString", "查询数据（字符串字段被声明为布尔）",
            BOOL_AS_STRING, _declared(active={"type": "boolean"}),
            [_cmp("active", "boolean", "string", True,
                  note="取值覆盖 \"true\" / \"false\" / \"1\" / \"0\" 四种布尔字符串写法")],
            "文档声明 active 为 boolean，实际返回字符串，取值覆盖 「true」「false」「1」「0」 四种写法"),
        _endpoint(
            "/bool-as-string-consistent", "listBoolAsStringConsistent",
            "查询数据（布尔字段对照组：实际也是真布尔）",
            BOOL_AS_BOOL, _declared(active={"type": "boolean"}),
            [_cmp("active", "boolean", "boolean", False,
                  note="对照组：与 /bool-as-string 同一份文档声明，实际返回真布尔，下游应当正常")],
            "与 /bool-as-string 同一份文档声明（active 为 boolean），实际也返回真布尔。"
            "两者对照：真布尔时下游正常、字符串时下游读取报类型转换错误"),
        _endpoint(
            "/datetime-as-epoch", "listDatetimeAsEpoch", "查询数据（时间字段返回毫秒时间戳）",
            DATETIME_AS_EPOCH,
            _declared(updated_at={"type": "string", "format": "date-time"}),
            [_cmp("updated_at", "string", "integer", True, declared_format="date-time")],
            "文档声明 updated_at 为 string / date-time，实际返回整数毫秒时间戳"),
        _endpoint(
            "/object-as-string", "listObjectAsString", "查询数据（对象字段返回 JSON 文本）",
            OBJECT_AS_STRING,
            _declared(extra={"type": "object", "properties": {
                "color": {"type": "string"}, "size": {"type": "integer"}}}),
            [_cmp("extra", "object", "string", True)],
            "文档声明 extra 为 object（带 properties），实际返回 JSON 字符串化的文本"),
        _endpoint(
            "/int-vs-decimal", "listIntVsDecimal", "查询数据（整数字段返回小数）",
            INT_VS_DECIMAL, _declared(price={"type": "integer"}),
            [_cmp("price", "integer", "number", False,
                  note="负例：后端把整数与小数都归为数值类型，不该判不兼容")],
            "文档声明 price 为 integer，实际返回小数 12.5。**预期不算不兼容**"),
        _endpoint(
            "/nullable-mostly-null", "listNullableMostlyNull", "查询数据（可空字段大多为 null）",
            NULLABLE_MOSTLY_NULL,
            _declared(remark={"type": "string", "nullable": True}),
            [_cmp("remark", "string", "string", False,
                  note="负例：6 条里 5 条为 null、1 条为字符串，不该判不兼容")],
            "文档声明 remark 为 string + nullable，实际 6 条里 5 条是 null、1 条是字符串。"
            "**预期不算不兼容**"),
        _endpoint(
            "/consistent", "listConsistent", "查询数据（对照组：文档与实际完全一致）",
            CONSISTENT,
            _declared(seq={"type": "integer"}, price={"type": "number"},
                      active={"type": "boolean"},
                      updated_at={"type": "string", "format": "date-time"},
                      remark={"type": "string", "nullable": True}),
            [_cmp("id", "string", "string", False),
             _cmp("seq", "integer", "integer", False),
             _cmp("price", "number", "number", False),
             _cmp("active", "boolean", "boolean", False),
             _cmp("updated_at", "string", "string", False, declared_format="date-time"),
             _cmp("remark", "string", "string", False)],
            "对照组：字段覆盖 string / integer / number / boolean / date-time / nullable string，"
            "文档声明与实际返回完全一致"),
    ]
    return [Scenario(
        "type-mismatch", "字段类型不一致（刻意）",
        "验证接口时把真实响应与已保存字段类型做兼容性比对：文档声明与实际返回故意不一致"
        "（全项目唯一，含 2 个不该判不兼容的负例与 1 个对照组）",
        endpoints, auth=NO_AUTH)]
