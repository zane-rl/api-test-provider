# -*- coding: utf-8 -*-
"""E 类：带故障开关的两个补测场景（均无鉴权），开关用 `faultswitch.SwitchBoard`。

- `depend-two-roots`：一个下游同时依赖两个互不依赖的根；三个接口各有开关，缺省正常。
- `page-fail-midway`：页码分页，**缺省就是「第 3 页起失败」**，切到 normal 才全通。

开关与计数只在真实请求里生效，生成文档时（`ctx.doc_mode`）一律跳过。
"""

from datetime import timedelta

from . import faultswitch
from .common import (
    Endpoint, Scenario, NO_AUTH, ApiError,
    build_dataset, iso, BASE_TIME, json_resp, qp,
)


def _page(rows):
    return json_resp({"code": 0, "message": "ok", "data": {"items": rows, "total": len(rows)}})


# ==================== depend-two-roots ====================

TWO_ROOTS = "depend-two-roots"

TR_WAREHOUSES = [
    {"id": "TRW-%05d" % i, "name": "%d 号仓" % i, "city": ["上海", "杭州", "成都"][i - 1],
     "capacity": 1000 * i, "active": True}
    for i in (1, 2, 3)
]
TR_PERIODS = [
    {"code": "2026-%02d" % m, "name": "2026 年 %d 月" % m,
     "starts_on": iso(BASE_TIME + timedelta(days=31 * (m - 1)))[:10], "closed": m == 1}
    for m in (1, 2)
]
TR_STOCK_PER_COMBINATION = 2


def stock_of(warehouse_id, period):
    """一个（仓库, 期间）组合的库存：恒定 2 条。取值不认识返回空列表。"""
    if not any(w["id"] == warehouse_id for w in TR_WAREHOUSES):
        return []
    if not any(p["code"] == period for p in TR_PERIODS):
        return []
    seq = int(warehouse_id.rsplit("-", 1)[1])
    month = int(period.rsplit("-", 1)[1])
    return [
        {"stock_id": "%s-%s-S%d" % (warehouse_id, period, j),
         "warehouse_id": warehouse_id, "period": period,
         "sku": "SKU-%03d" % (seq * 10 + j),
         "quantity": seq * 100 + month * 10 + j,
         "amount": round(seq * 99.5 + month * 3.25 + j, 2)}
        for j in range(1, TR_STOCK_PER_COMBINATION + 1)
    ]


TR_ALL_STOCK = [s for w in TR_WAREHOUSES for p in TR_PERIODS for s in stock_of(w["id"], p["code"])]

TR_BOARD = faultswitch.register(faultswitch.SwitchBoard(TWO_ROOTS, [
    ("warehouses", "GET", "/warehouses"),
    ("periods", "GET", "/periods"),
    ("stock", "GET", "/stock"),
]))


def _scenario_two_roots():
    def warehouses(ctx):
        return _page(TR_WAREHOUSES)

    def periods(ctx):
        return _page(TR_PERIODS)

    def stock(ctx):
        warehouse_id, period = ctx.query.get("warehouse_id"), ctx.query.get("period")
        if not warehouse_id:
            raise ApiError(400, "缺少必填参数 warehouse_id")
        if not period:
            raise ApiError(400, "缺少必填参数 period")
        return _page(stock_of(warehouse_id, period))

    switch_note = "开关键 %%s，见 /__admin/faults?scenario=%s" % TWO_ROOTS
    reference = len(TR_ALL_STOCK)
    endpoints = [
        Endpoint("GET", "/warehouses", TR_BOARD.guarded("warehouses", warehouses),
                 operation_id="listTwoRootsWarehouses", summary="查询仓库列表（根 R1）",
                 description="不分页，共 %d 条；与 /periods 互不依赖" % len(TR_WAREHOUSES),
                 record_path="data.items", primary_key="id",
                 dataset=lambda: list(TR_WAREHOUSES), doc_sample={},
                 note=switch_note % "warehouses",
                 extra_catalog={"faultKey": "warehouses"}),
        Endpoint("GET", "/periods", TR_BOARD.guarded("periods", periods),
                 operation_id="listTwoRootsPeriods", summary="查询期间列表（根 R2）",
                 description="不分页，共 %d 条；与 /warehouses 互不依赖" % len(TR_PERIODS),
                 record_path="data.items", primary_key="code",
                 dataset=lambda: list(TR_PERIODS), doc_sample={},
                 note=switch_note % "periods",
                 extra_catalog={"faultKey": "periods"}),
        Endpoint("GET", "/stock", TR_BOARD.guarded("stock", stock),
                 operation_id="listTwoRootsStock", summary="查询库存（下游 X，同时依赖 R1 与 R2）",
                 description="查询参数 warehouse_id 取自 /warehouses 的 id，period 取自 /periods 的 code，"
                             "两个都必填，缺任一个返回 400；每个（仓库, 期间）组合恒定返回 %d 条，"
                             "取值不认识返回空列表" % TR_STOCK_PER_COMBINATION,
                 parameters=[qp("warehouse_id", required=True,
                                description="仓库 ID，来自 /warehouses 的 id 字段"),
                             qp("period", required=True,
                                description="期间编码，来自 /periods 的 code 字段")],
                 record_path="data.items", primary_key="stock_id",
                 dataset=lambda: list(TR_ALL_STOCK),
                 doc_sample={"query": {"warehouse_id": "TRW-00001", "period": "2026-01"}},
                 note="每个（warehouse_id, period）组合返回 %d 条；expectedTotal %d 只是参考值："
                      "按 %d 个仓库与 %d 个期间两两组合各调一次算出，后端实际怎么组合不在这里规定；"
                      % (TR_STOCK_PER_COMBINATION, reference, len(TR_WAREHOUSES), len(TR_PERIODS))
                      + switch_note % "stock",
                 extra_catalog={
                     "faultKey": "stock",
                     "rowsPerCombination": TR_STOCK_PER_COMBINATION,
                     "referenceTotalIfAllCombinations": reference,
                     "dependsOn": [
                         {"path": "/warehouses", "field": "id",
                          "param": {"name": "warehouse_id", "in": "QUERY"}},
                         {"path": "/periods", "field": "code",
                          "param": {"name": "period", "in": "QUERY"}},
                     ]}),
    ]
    return Scenario(TWO_ROOTS, "一个下游依赖两个根",
                    "下游的两个参数分别取自两个互不依赖的根接口；三个接口各自可切换正常 / 持续失败",
                    endpoints, auth=NO_AUTH,
                    description="两个互不依赖的根接口 /warehouses、/periods，下游 /stock 的两个必填查询参数"
                                "分别取自它们；每个接口可由 /__admin/faults 在运行期切到持续失败"
                                "（默认 503）或切回正常")


# ==================== page-fail-midway ====================

PAGE_FAIL = "page-fail-midway"

PF_ROWS = build_dataset("PFM", 50)
PF_PAGE_SIZE = 10
PF_FAIL_FROM_PAGE = 3
PF_DEFAULT_STATUS = 500

# 注意缺省模式是 fail：复位之后回到「第 3 页起失败」
PF_BOARD = faultswitch.register(faultswitch.SwitchBoard(
    PAGE_FAIL, [("records", "GET", "/records")],
    default_mode=faultswitch.MODE_FAIL, default_status=PF_DEFAULT_STATUS))


def _scenario_page_fail_midway():
    def records(ctx):
        page = ctx.int_query("page", 1)
        size = max(1, min(ctx.int_query("page_size", PF_PAGE_SIZE), 200))
        start = (page - 1) * size
        if start < 0:
            raise ApiError(400, "页码不能小于 1")
        chunk = PF_ROWS[start:start + size]
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": chunk, "page": page, "page_size": size,
                                   "total": len(PF_ROWS),
                                   "has_next": start + size < len(PF_ROWS)}})

    def handler(ctx):
        # 生成文档时不看开关、不计数
        if ctx.doc_mode:
            return records(ctx)
        status = PF_BOARD.fail_status("records")
        # 失败模式只拦第 3 页及之后；页码不是整数时交给下面的参数校验去报 400
        raw_page = ctx.query.get("page") or "1"
        if status is not None and raw_page.lstrip("-").isdigit() and int(raw_page) >= PF_FAIL_FROM_PAGE:
            return PF_BOARD.failure("records", status)
        return PF_BOARD.counted("records", records, ctx)

    warning = ("【不是正常场景】缺省状态下第 1、2 页正常，第 %d 页起恒定返回 %d，"
               "一次完整同步必然中途失败；用 /__admin/faults?scenario=%s 把开关 records 切到 normal 后 "
               "%d 页才全部正常；POST /__admin/reset?scenario=%s 会回到缺省的失败状态"
               % (PF_FAIL_FROM_PAGE, PF_DEFAULT_STATUS, PAGE_FAIL, len(PF_ROWS) // PF_PAGE_SIZE, PAGE_FAIL))
    before_failure = (PF_FAIL_FROM_PAGE - 1) * PF_PAGE_SIZE
    ep = Endpoint(
        "GET", "/records", handler,
        operation_id="listPageFailMidwayRecords", summary="查询记录（页码分页，第 3 页起失败）",
        description="%s。共 %d 条，默认每页 %d 条" % (warning, len(PF_ROWS), PF_PAGE_SIZE),
        parameters=[qp("page", "integer", description="页码，从 1 开始"),
                    qp("page_size", "integer", description="每页数量，默认 %d" % PF_PAGE_SIZE)],
        record_path="data.items", primary_key="id",
        dataset=lambda: list(PF_ROWS),
        pagination={"type": "PAGE_NUMBER",
                    "pageParam": {"name": "page", "in": "QUERY"},
                    "pageSizeParam": {"name": "page_size", "in": "QUERY"},
                    "startFrom": 1, "defaultPageSize": PF_PAGE_SIZE,
                    "totalPath": "data.total", "hasMorePath": "data.has_next"},
        doc_sample={"query": {"page": "1", "page_size": str(PF_PAGE_SIZE)}},
        note="缺省第 %d 页起返回 %d；expectedTotal %d 是开关切到 normal 后的条数，"
             "缺省状态下失败前只能取到 %d 条；开关键 records，见 /__admin/faults?scenario=%s"
             % (PF_FAIL_FROM_PAGE, PF_DEFAULT_STATUS, len(PF_ROWS), before_failure, PAGE_FAIL),
        extra_catalog={"faultKey": "records", "defaultMode": faultswitch.MODE_FAIL,
                       "failFromPage": PF_FAIL_FROM_PAGE, "rowsBeforeFailure": before_failure},
    )
    return Scenario(PAGE_FAIL, "分页中途失败", "页码分页取到第 3 页起失败；修复（切开关）后可完整取完",
                    [ep], auth=NO_AUTH, description=warning)


def build_scenarios():
    return [_scenario_two_roots(), _scenario_page_fail_midway()]
