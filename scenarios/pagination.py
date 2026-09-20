# -*- coding: utf-8 -*-
"""B 类：分页场景（均无鉴权）。

数据条数刻意不是每页数量的整数倍，用来验证「最后一页不足一页即停」与「结束条件」。
"""

from .common import (
    Endpoint, Scenario, ApiError, NO_AUTH,
    build_dataset, build_record, iso, BASE_TIME,
    json_resp, qp, hp, json_body,
)
from datetime import timedelta

# ==================== 数据集（固定公式，重启一致） ====================

WAREHOUSES = build_dataset("WH", 4)
PRODUCTS = build_dataset("PRD", 57, id_field="sku")
EVENTS = build_dataset("EVT", 73, id_field="event_id")
SUPPLIERS = build_dataset("SUP", 41)
BODY_PRODUCTS = build_dataset("BPD", 45, id_field="sku")

# 库存流水：刻意不带主键字段，用来验证后端的合成主键
MOVEMENTS = [
    {
        "warehouse": WAREHOUSES[i % len(WAREHOUSES)]["id"],
        "sku": "PRD-%05d" % ((i % 57) + 1),
        "type": ["IN", "OUT", "TRANSFER"][i % 3],
        "quantity": (i % 40) + 1,
        "unit_cost": round(3.5 + i * 0.77, 2),
        "confirmed": i % 4 != 0,
        "occurred_at": iso(BASE_TIME + timedelta(minutes=i * 7)),
        "note": None if i % 5 == 0 else "流水备注%d" % i,
        "operator": {"id": "OP-%02d" % (i % 6), "name": "操作员%d" % (i % 6)},
    }
    for i in range(1, 104)
]

MAX_PAGE_SIZE = 200


def _clamp_size(size):
    return max(1, min(size, MAX_PAGE_SIZE))


def _encode_cursor(offset):
    return "cur-%d" % offset


def _decode_cursor(cursor):
    if cursor in (None, ""):
        return 0
    if not str(cursor).startswith("cur-"):
        raise ApiError(400, "游标格式错误：%r，应形如 cur-20" % cursor)
    try:
        return int(str(cursor)[4:])
    except ValueError:
        raise ApiError(400, "游标格式错误：%r，应形如 cur-20" % cursor)


# ==================== root-array：根数组、不分页 ====================

def _scenario_root_array():
    def handler(ctx):
        return json_resp(WAREHOUSES)

    ep = Endpoint(
        "GET", "/warehouses", handler,
        operation_id="listWarehouses", summary="查询仓库列表",
        description="数组直接作根，没有任何包裹层，一次取完 %d 条" % len(WAREHOUSES),
        record_path="", primary_key="id", dataset=lambda: list(WAREHOUSES),
        pagination={"type": "NONE"}, doc_sample={},
    )
    return Scenario("root-array", "根数组不分页", "记录路径 = 根数组；分页方式 NONE",
                    [ep], auth=NO_AUTH)


# ==================== page-number：页码分页三种形态 ====================

def _page_payload(rows, page, size, start_from):
    start = (page - start_from) * size
    if start < 0:
        raise ApiError(400, "页码不能小于起始页 %d" % start_from)
    chunk = rows[start:start + size]
    return {
        "code": 0, "message": "ok",
        "data": {"items": chunk, "page": page, "page_size": size,
                 "total": len(rows), "has_next": start + size < len(rows)},
    }


def _scenario_page_number():
    def products(ctx):
        page = ctx.int_query("page", 1)
        size = _clamp_size(ctx.int_query("page_size", 20))
        return json_resp(_page_payload(PRODUCTS, page, size, 1))

    def products0(ctx):
        page = ctx.int_query("page", 0)
        size = _clamp_size(ctx.int_query("page_size", 20))
        return json_resp(_page_payload(PRODUCTS, page, size, 0))

    def products_nosize(ctx):
        # 刻意不接受 page_size：后端若误发，这里 400 让问题可见，而不是静默忽略
        if "page_size" in ctx.query:
            raise ApiError(400, "本接口不接受 page_size 参数，每页固定 10 条")
        page = ctx.int_query("page", 1)
        start = (page - 1) * 10
        if start < 0:
            raise ApiError(400, "页码不能小于 1")
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": PRODUCTS[start:start + 10], "page": page}})

    common_params = [qp("page", "integer", description="页码"),
                     qp("page_size", "integer", description="每页数量，最大 %d" % MAX_PAGE_SIZE)]
    pg = {"pageParam": {"name": "page", "in": "QUERY"},
          "pageSizeParam": {"name": "page_size", "in": "QUERY"},
          "defaultPageSize": 20, "totalPath": "data.total", "hasMorePath": "data.has_next"}

    endpoints = [
        Endpoint("GET", "/products", products,
                 operation_id="listProducts", summary="查询商品列表（页码从 1 开始）",
                 description="页码分页，共 %d 条；结束条件可用 data.has_next 或 data.total" % len(PRODUCTS),
                 parameters=common_params, record_path="data.items", primary_key="sku",
                 dataset=lambda: list(PRODUCTS),
                 pagination=dict(pg, type="PAGE_NUMBER", startFrom=1),
                 doc_sample={"query": {"page": "1", "page_size": "20"}}),
        Endpoint("GET", "/products0", products0,
                 operation_id="listProductsFromPageZero", summary="查询商品列表（页码从 0 开始）",
                 description="与 /products 数据相同，但页码起始值为 0",
                 parameters=common_params, record_path="data.items", primary_key="sku",
                 dataset=lambda: list(PRODUCTS),
                 pagination=dict(pg, type="PAGE_NUMBER", startFrom=0),
                 doc_sample={"query": {"page": "0", "page_size": "20"}}),
        Endpoint("GET", "/products-nosize", products_nosize,
                 operation_id="listProductsFixedSize", summary="查询商品列表（固定每页 10 条）",
                 description="不接受 page_size（带了返回 400）、响应无 total，靠返回空页结束",
                 parameters=[qp("page", "integer", description="页码，从 1 开始")],
                 record_path="data.items", primary_key="sku",
                 dataset=lambda: list(PRODUCTS),
                 pagination={"type": "PAGE_NUMBER", "pageParam": {"name": "page", "in": "QUERY"},
                             "pageSizeParam": None, "startFrom": 1, "fixedPageSize": 10,
                             "totalPath": None, "hasMorePath": None,
                             "stopCondition": "EMPTY_PAGE"},
                 doc_sample={"query": {"page": "1"}},
                 note="带 page_size 会返回 400"),
    ]
    return Scenario("page-number", "页码分页", "PAGE_NUMBER × QUERY；startFrom 1/0；不配每页数量靠空页结束",
                    endpoints, auth=NO_AUTH, swagger2=True)


# ==================== offset：偏移分页、记录无主键 ====================

def _scenario_offset():
    def handler(ctx):
        offset = ctx.int_query("offset", 0, minimum=0)
        limit = _clamp_size(ctx.int_query("limit", 20))
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": MOVEMENTS[offset:offset + limit],
                                   "offset": offset, "limit": limit,
                                   "total": len(MOVEMENTS)}})

    ep = Endpoint(
        "GET", "/movements", handler,
        operation_id="listMovements", summary="查询库存流水",
        description="偏移分页，共 %d 条；记录没有主键字段，用于验证合成主键" % len(MOVEMENTS),
        parameters=[qp("offset", "integer", description="偏移量，从 0 开始"),
                    qp("limit", "integer", description="每页数量")],
        record_path="data.items", primary_key=None, dataset=lambda: list(MOVEMENTS),
        pagination={"type": "OFFSET", "offsetParam": {"name": "offset", "in": "QUERY"},
                    "limitParam": {"name": "limit", "in": "QUERY"}, "startFrom": 0,
                    "defaultLimit": 20, "totalPath": "data.total"},
        doc_sample={"query": {"offset": "0", "limit": "20"}},
        note="记录无主键字段",
    )
    return Scenario("offset", "偏移分页", "OFFSET × QUERY；totalPath 结束；记录无主键",
                    [ep], auth=NO_AUTH)


# ==================== cursor：游标三种位置 ====================

def _cursor_payload(cursor, limit):
    offset = _decode_cursor(cursor)
    limit = _clamp_size(limit)
    chunk = EVENTS[offset:offset + limit]
    has_more = offset + limit < len(EVENTS)
    return {"data": chunk,
            "next_cursor": _encode_cursor(offset + limit) if has_more else None,
            "has_more": has_more}


def _cursor_pagination(param_in, name="cursor"):
    return {"type": "CURSOR", "cursorParam": {"name": name, "in": param_in},
            "limitParam": {"name": "limit", "in": param_in if param_in == "BODY" else "QUERY"},
            "defaultLimit": 20, "nextCursorPath": "next_cursor", "hasMorePath": "has_more"}


def _scenario_cursor_query():
    def handler(ctx):
        return json_resp(_cursor_payload(ctx.query.get("cursor"), ctx.int_query("limit", 20)))

    ep = Endpoint(
        "GET", "/events", handler,
        operation_id="listEvents", summary="查询事件列表（游标在 Query）",
        description="游标分页，共 %d 条；末页 next_cursor 为 null" % len(EVENTS),
        parameters=[qp("cursor", "string", description="分页游标，首页不传"),
                    qp("limit", "integer", description="每页数量")],
        record_path="data", primary_key="event_id", dataset=lambda: list(EVENTS),
        pagination=_cursor_pagination("QUERY"),
        doc_sample={"query": {"limit": "20"}},
    )
    return Scenario("cursor-query", "游标分页（Query）", "CURSOR × QUERY；next_cursor 为 null 结束",
                    [ep], auth=NO_AUTH)


def _scenario_cursor_header():
    def handler(ctx):
        return json_resp(_cursor_payload(ctx.header("X-Cursor"), ctx.int_query("limit", 20)))

    ep = Endpoint(
        "GET", "/events", handler,
        operation_id="listEventsByHeaderCursor", summary="查询事件列表（游标在请求头）",
        description="游标通过请求头 X-Cursor 传入，每页数量仍在 Query",
        parameters=[hp("X-Cursor", "string", description="分页游标，首页不传"),
                    qp("limit", "integer", description="每页数量")],
        record_path="data", primary_key="event_id", dataset=lambda: list(EVENTS),
        pagination=_cursor_pagination("HEADER", name="X-Cursor"),
        doc_sample={"query": {"limit": "20"}},
    )
    return Scenario("cursor-header", "游标分页（请求头）", "CURSOR × HEADER",
                    [ep], auth=NO_AUTH)


def _scenario_cursor_body():
    def handler(ctx):
        body = ctx.json_body_or_400()
        limit = body.get("limit", 20)
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ApiError(400, "请求体 limit 必须是 JSON 数值，收到：%r" % (limit,))
        return json_resp(_cursor_payload(body.get("cursor"), limit))

    ep = Endpoint(
        "POST", "/events/search", handler,
        operation_id="searchEvents", summary="查询事件列表（游标在请求体）",
        description="游标与每页数量都写在 JSON 请求体里",
        request_body=json_body(
            {"type": "object", "properties": {
                "cursor": {"type": "string", "nullable": True},
                "limit": {"type": "integer"},
                "filter": {"type": "object", "properties": {"type": {"type": "string"}}}}},
            {"cursor": None, "limit": 20, "filter": {"type": "all"}}),
        record_path="data", primary_key="event_id", dataset=lambda: list(EVENTS),
        pagination=_cursor_pagination("BODY"),
        doc_sample={"body": {"limit": 20, "filter": {"type": "all"}}},
    )
    return Scenario("cursor-body", "游标分页（请求体）", "CURSOR × BODY（E3-D1 修复后的能力）",
                    [ep], auth=NO_AUTH)


# ==================== next-url：下一页地址三种写法 ====================

NEXT_URL_PAGE_SIZE = 15


def _suppliers_chunk(page):
    start = (page - 1) * NEXT_URL_PAGE_SIZE
    if start < 0:
        raise ApiError(400, "页码不能小于 1")
    chunk = SUPPLIERS[start:start + NEXT_URL_PAGE_SIZE]
    has_next = start + NEXT_URL_PAGE_SIZE < len(SUPPLIERS)
    return chunk, has_next


def _scenario_next_url():
    def same_origin(ctx):
        page = ctx.int_query("page", 1)
        chunk, has_next = _suppliers_chunk(page)
        nxt = "%s/suppliers?page=%d" % (ctx.base_url, page + 1) if has_next else None
        return json_resp({"data": chunk, "next": nxt})

    def relative(ctx):
        page = ctx.int_query("page", 1)
        chunk, has_next = _suppliers_chunk(page)
        nxt = "/s/next-url/suppliers-relative?page=%d" % (page + 1) if has_next else None
        return json_resp({"data": chunk, "next": nxt})

    def cross_host(ctx):
        page = ctx.int_query("page", 1)
        chunk, has_next = _suppliers_chunk(page)
        # 指向 localhost，与文档里的 127.0.0.1 不同源：后端应拒绝跟随
        nxt = None
        if has_next:
            nxt = "%s/s/next-url/suppliers-crosshost?page=%d" % (
                ctx.base_url.replace("127.0.0.1", "localhost").split("/s/")[0], page + 1)
        return json_resp({"data": chunk, "next": nxt})

    page_param = [qp("page", "integer", description="页码，首页不传；后续请求应直接使用 next 给出的地址")]
    base_pg = {"type": "NEXT_URL", "nextUrlPath": "next", "pageSize": NEXT_URL_PAGE_SIZE}

    endpoints = [
        Endpoint("GET", "/suppliers", same_origin,
                 operation_id="listSuppliers", summary="查询供应商列表（下一页为同源绝对地址）",
                 description="共 %d 条，每页 %d 条；末页 next 为 null" % (len(SUPPLIERS), NEXT_URL_PAGE_SIZE),
                 parameters=page_param, record_path="data", primary_key="id",
                 dataset=lambda: list(SUPPLIERS),
                 pagination=dict(base_pg, nextUrlStyle="ABSOLUTE_SAME_ORIGIN"),
                 doc_sample={}),
        Endpoint("GET", "/suppliers-relative", relative,
                 operation_id="listSuppliersRelativeNext", summary="查询供应商列表（下一页为相对地址）",
                 description="next 是相对地址，后端应按 RFC 3986 相对引用补全",
                 parameters=page_param, record_path="data", primary_key="id",
                 dataset=lambda: list(SUPPLIERS),
                 pagination=dict(base_pg, nextUrlStyle="RELATIVE"),
                 doc_sample={}),
        Endpoint("GET", "/suppliers-crosshost", cross_host,
                 operation_id="listSuppliersCrossHostNext", summary="查询供应商列表（下一页跨主机）",
                 description="next 指向 localhost（与文档里的 127.0.0.1 不同源），后端应拒绝跟随",
                 parameters=page_param, record_path="data", primary_key="id",
                 dataset=lambda: list(SUPPLIERS),
                 pagination=dict(base_pg, nextUrlStyle="ABSOLUTE_CROSS_ORIGIN",
                                 expectedRecordsIfRefused=NEXT_URL_PAGE_SIZE),
                 doc_sample={},
                 note="后端若正确拒绝跟随，只应取到首页 %d 条；expectedTotal 给的是数据总量"
                      % NEXT_URL_PAGE_SIZE),
    ]
    return Scenario("next-url", "下一页地址分页", "NEXT_URL：同源绝对 / 相对 / 跨主机三种",
                    endpoints, auth=NO_AUTH)


# ==================== body-page：请求体页码分页 + 类型校验 ====================

def _scenario_body_page():
    def handler(ctx):
        body = ctx.json_body_or_400()
        page = body.get("page", 1)
        size = body.get("size", 20)
        for name, value in (("page", page), ("size", size)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ApiError(400, "请求体 %s 必须是 JSON 数值（不带引号），收到：%r（%s）"
                               % (name, value, type(value).__name__))
        size = _clamp_size(size)
        keyword = body.get("keyword") or ""
        rows = [p for p in BODY_PRODUCTS if not keyword or keyword in p["name"]]
        start = (page - 1) * size
        if start < 0:
            raise ApiError(400, "page 不能小于 1")
        chunk = rows[start:start + size]
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": chunk, "page": page, "size": size,
                                   "total": len(rows), "has_next": start + size < len(rows)}})

    ep = Endpoint(
        "POST", "/products/query", handler,
        operation_id="queryProducts", summary="按条件搜索商品（页码在请求体）",
        description="共 %d 条；page 与 size 必须是 JSON 数值，写成字符串返回 400" % len(BODY_PRODUCTS),
        request_body=json_body(
            {"type": "object", "properties": {
                "page": {"type": "integer"}, "size": {"type": "integer"},
                "keyword": {"type": "string"}}},
            {"page": 1, "size": 20, "keyword": ""}),
        record_path="data.items", primary_key="sku", dataset=lambda: list(BODY_PRODUCTS),
        pagination={"type": "PAGE_NUMBER", "pageParam": {"name": "page", "in": "BODY"},
                    "pageSizeParam": {"name": "size", "in": "BODY"}, "startFrom": 1,
                    "defaultPageSize": 20, "totalPath": "data.total",
                    "hasMorePath": "data.has_next"},
        doc_sample={"body": {"page": 1, "size": 20, "keyword": ""}},
        note="page / size 写成字符串返回 400，用于守 KEY_VALUE 请求体的类型保真",
    )
    return Scenario("body-page", "请求体页码分页", "PAGE_NUMBER × BODY + 请求体值类型保真",
                    [ep], auth=NO_AUTH)


def build_scenarios():
    return [
        _scenario_root_array(),
        _scenario_page_number(),
        _scenario_offset(),
        _scenario_cursor_query(),
        _scenario_cursor_header(),
        _scenario_cursor_body(),
        _scenario_next_url(),
        _scenario_body_page(),
    ]
