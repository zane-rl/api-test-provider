# -*- coding: utf-8 -*-
"""E 类：故障注入场景（无鉴权）。

照搬现有 mock 的 8 个故障端点，另加一个 302 重定向。
所有计数器可由 `POST /__admin/reset` 复位，保证用例可重复执行。

这些端点有副作用（计数、sleep），因此**不参与「调用一次 handler 推断响应 schema」**，
统一显式声明 schema 与示例。
"""

import threading
import time

from .common import (
    Endpoint, Scenario, NO_AUTH, build_dataset, json_resp, Resp, qp,
)

FAULT_ROWS = build_dataset("FLT", 5)
# 慢响应端点单独一份数据：后端要从这里推断记录结构，不能是空数组
SLOW_ROWS = build_dataset("SLOW", 5)

_counters = {}
_lock = threading.Lock()


def _bump(key):
    with _lock:
        _counters[key] = _counters.get(key, 0) + 1
        return _counters[key]


def reset_counters():
    with _lock:
        _counters.clear()


def counters_snapshot():
    with _lock:
        return dict(_counters)


# 故障端点的响应 schema 显式声明（它们不能在生成文档时被调用）
_ITEMS_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "integer"},
        "message": {"type": "string"},
        "data": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object"}},
            "total": {"type": "integer"}}},
    },
}
_ERROR_SCHEMA = {"type": "object", "properties": {
    "code": {"type": "integer"}, "message": {"type": "string"}}}


def _ok_payload(rows):
    return {"code": 0, "message": "ok", "data": {"items": rows, "total": len(rows)}}


def build_scenarios():
    def always_500(ctx):
        return json_resp({"code": 500, "message": "internal error"}, status=500)

    def flaky(ctx):
        n = _bump("flaky")
        if n % 3 != 0:
            return json_resp({"code": 503, "message": "service unavailable（第 %d 次）" % n},
                             status=503)
        return json_resp(_ok_payload(FAULT_ROWS))

    def biz_error(ctx):
        return json_resp({"code": 5001, "message": "配额不足", "data": None})

    def rate_limit(ctx):
        n = _bump("rate-limit")
        if n % 2 == 1:
            return json_resp({"code": 429, "message": "too many requests"},
                             status=429, headers={"Retry-After": "2"})
        return json_resp(_ok_payload(FAULT_ROWS[:3]))

    def slow(ctx):
        seconds = max(0, min(ctx.int_query("seconds", 5), 60))
        # doc_mode 是生成文档时的假上下文，在那里 sleep 会把取文档拖住
        if seconds and not ctx.doc_mode:
            time.sleep(seconds)
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": SLOW_ROWS, "total": len(SLOW_ROWS),
                                   "slept": seconds}})

    def not_json(ctx):
        return Resp(status=200, raw=b"<html><body>gateway error</body></html>",
                    content_type="text/html; charset=utf-8")

    def huge(ctx):
        filler = "x" * 1024
        return json_resp({"code": 0, "message": "ok", "data": {
            "items": [{"id": i, "blob": filler} for i in range(3000)], "total": 3000}})

    def drifted(ctx):
        rows = []
        for row in FAULT_ROWS:
            copy = dict(row)
            copy.pop("price", None)      # 少一个字段
            copy["extra_note"] = "新增字段"  # 多一个字段
            rows.append(copy)
        return json_resp(_ok_payload(rows))

    def redirect(ctx):
        return Resp(status=302, payload={"code": 302, "message": "moved"},
                    headers={"Location": "/s/faults/ok"})

    def ok(ctx):
        return json_resp(_ok_payload(FAULT_ROWS))

    def fault(path, handler, op, summary, desc, schema=_ITEMS_SCHEMA, example=None,
              parameters=(), note=""):
        return Endpoint("GET", path, handler, operation_id=op, summary=summary,
                        description=desc, role="FAULT", parameters=list(parameters),
                        record_path="data.items", primary_key="id", expected_total=None,
                        response_schema=schema, response_example=example,
                        doc_sample=None, note=note)

    endpoints = [
        fault("/always-500", always_500, "listAlwaysFail", "恒定 500",
              "每次都返回 HTTP 500，验证重试耗尽后判失败",
              schema=_ERROR_SCHEMA, example={"code": 500, "message": "internal error"},
              note="恒定 500"),
        fault("/flaky", flaky, "listFlaky", "前 2 次 503、第 3 次成功",
              "按调用次数：第 1、2 次 503，第 3 次 200，验证退避重试后自愈",
              example=_ok_payload(FAULT_ROWS[:2]), note="计数器 flaky，可 reset 复位"),
        fault("/biz-error", biz_error, "listBizError", "HTTP 200 但业务码非 0",
              "HTTP 成功、code=5001，验证业务成功条件判定",
              schema={"type": "object", "properties": {
                  "code": {"type": "integer"}, "message": {"type": "string"},
                  "data": {"type": "object", "nullable": True}}},
              example={"code": 5001, "message": "配额不足", "data": None}),
        fault("/rate-limit", rate_limit, "listRateLimited", "隔次 429 + Retry-After",
              "奇数次返回 429 并带 Retry-After: 2，偶数次成功",
              example=_ok_payload(FAULT_ROWS[:3]), note="计数器 rate-limit，可 reset 复位"),
        # 与其它故障端点不同：这个要返回真实数据，后端得从中推断记录结构才能走完整条链路。
        # 它没有计数器之类的副作用，所以 schema 交给「真的调一次处理函数」去推断。
        Endpoint("GET", "/slow", slow,
                 operation_id="listSlow", summary="慢响应（数据正常）",
                 description="按 seconds 参数延迟后返回 %d 条记录（上限 60 秒），验证超时配置；"
                             "响应结构与其它列表端点一致" % len(SLOW_ROWS),
                 role="FAULT",
                 parameters=[qp("seconds", "integer", description="延迟秒数，默认 5，上限 60")],
                 record_path="data.items", primary_key="id",
                 dataset=lambda: list(SLOW_ROWS),
                 doc_sample={"query": {"seconds": "0"}},
                 note="延迟可控，数据恒定 %d 条" % len(SLOW_ROWS)),
        fault("/not-json", not_json, "listNotJson", "返回 HTML 而不是 JSON",
              "验证「响应不是合法 JSON」的提示",
              schema={"type": "string"}, example="<html><body>gateway error</body></html>"),
        fault("/huge", huge, "listHuge", "约 3.1MB 超大响应",
              "3000 条 1KB 记录，验证 2MB 截断提示",
              example={"code": 0, "message": "ok",
                       "data": {"items": [{"id": 0, "blob": "x...（1024 字符）"}], "total": 3000}}),
        fault("/drifted", drifted, "listDrifted", "字段结构漂移",
              "相对其它端点少 price、多 extra_note，验证结构变更诊断",
              example=_ok_payload([dict(FAULT_ROWS[0], extra_note="新增字段")])),
        fault("/redirect", redirect, "listRedirect", "302 重定向到 /s/faults/ok",
              "返回 302 且 Location 指向同场景的 /ok，后端不应跟随重定向",
              schema=_ERROR_SCHEMA, example={"code": 302, "message": "moved"},
              note="302 → /s/faults/ok"),
        Endpoint("GET", "/ok", ok, operation_id="listOk", summary="正常返回（重定向目标）",
                 description="恒定成功，%d 条记录" % len(FAULT_ROWS), role="FAULT",
                 record_path="data.items", primary_key="id",
                 dataset=lambda: list(FAULT_ROWS), doc_sample={}),
    ]
    return [Scenario("faults", "故障注入", "错误处理策略、重试、限流、超时、非 JSON、截断、结构漂移、重定向",
                     endpoints, auth=NO_AUTH)]
