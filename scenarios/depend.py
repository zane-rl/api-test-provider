# -*- coding: utf-8 -*-
"""D 类：依赖场景（均无鉴权）。

父接口提供记录，子接口按父记录的字段取数：
- `depend-path`：子接口通过**路径参数**依赖父记录的 sku
- `depend-body`：子接口通过**请求体字段**依赖父记录的 customerId
- `required-header-cookie`：子接口通过**必填请求头 / 必填 Cookie**依赖父记录的字段
- `depend-empty-parent`：父接口恒定返回 0 条
- `depend-mixed-key`：父记录的取值字段数字与文本混用；另有一个子接口的两个参数取自两个父接口

子接口每个父记录的条数刻意不同（公式见下方注释与 README），用于验证依赖展开后逐父取数。
"""

import json
from datetime import timedelta

from .common import (
    Endpoint, Scenario, NO_AUTH, ApiError,
    build_dataset, iso, BASE_TIME, json_resp, qp, hp, cp, json_body, pp,
)

# ==================== 数据集 ====================

DEP_PRODUCTS = build_dataset("DPD", 30, id_field="sku")
DEP_CUSTOMERS = build_dataset("CUS", 12)

DEP_PRODUCT_PAGE_SIZE = 20


def _seq_of(code):
    """从 `XXX-00007` 取出序号 7"""
    try:
        return int(code.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def batches_of(sku):
    """批次条数公式：`(序号 * 7) % 5`，取值 0~4 条。

    30 个商品合计 60 条批次（每 5 个商品一循环，合计 2+4+1+3+0=10 条）。
    """
    seq = _seq_of(sku)
    count = (seq * 7) % 5
    return [
        {
            "batch_no": "%s-B%d" % (sku, j),
            "sku": sku,
            "quantity": (seq * (j + 1) * 13) % 300,
            "qualified": (seq + j) % 3 != 0,
            "produced_on": iso(BASE_TIME + timedelta(days=seq + j))[:10],
            "expires_on": iso(BASE_TIME + timedelta(days=180 + seq - j * 10))[:10],
            "inspector": None if (seq + j) % 4 == 0 else "质检员%d" % ((seq + j) % 7),
        }
        for j in range(1, count + 1)
    ]


def orders_of(customer_id):
    """订单条数公式：`(序号 * 3) % 4`，取值 0~3 条。12 个客户合计 18 条订单。"""
    seq = _seq_of(customer_id)
    count = (seq * 3) % 4
    return [
        {
            "order_no": "%s-O%d" % (customer_id, j),
            "customer_id": customer_id,
            "amount": round(66.6 + seq * j * 7.3, 2),
            "paid": (seq + j) % 2 == 0,
            "placed_at": iso(BASE_TIME + timedelta(days=seq, hours=j)),
            "channel": ["WEB", "APP", "POS"][(seq + j) % 3],
            "coupon": None if (seq + j) % 3 == 0 else "CP-%03d" % ((seq * j) % 50),
        }
        for j in range(1, count + 1)
    ]


ALL_BATCHES = [b for p in DEP_PRODUCTS for b in batches_of(p["sku"])]
ALL_ORDERS = [o for c in DEP_CUSTOMERS for o in orders_of(c["id"])]


# ==================== depend-path ====================

def _scenario_depend_path():
    def products(ctx):
        page = ctx.int_query("page", 1)
        size = max(1, min(ctx.int_query("page_size", DEP_PRODUCT_PAGE_SIZE), 200))
        start = (page - 1) * size
        if start < 0:
            raise ApiError(400, "页码不能小于 1")
        chunk = DEP_PRODUCTS[start:start + size]
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": chunk, "page": page, "page_size": size,
                                   "total": len(DEP_PRODUCTS),
                                   "has_next": start + size < len(DEP_PRODUCTS)}})

    def batches(ctx):
        sku = ctx.path_params.get("sku")
        if not any(p["sku"] == sku for p in DEP_PRODUCTS):
            return json_resp({"code": 404, "message": "商品不存在：%s" % sku}, status=404)
        return json_resp({"code": 0, "message": "ok", "data": batches_of(sku)})

    endpoints = [
        Endpoint("GET", "/products", products,
                 operation_id="listDependProducts", summary="查询商品列表（依赖父接口）",
                 description="页码分页，共 %d 条" % len(DEP_PRODUCTS),
                 parameters=[qp("page", "integer", description="页码，从 1 开始"),
                             qp("page_size", "integer", description="每页数量")],
                 record_path="data.items", primary_key="sku",
                 dataset=lambda: list(DEP_PRODUCTS),
                 pagination={"type": "PAGE_NUMBER",
                             "pageParam": {"name": "page", "in": "QUERY"},
                             "pageSizeParam": {"name": "page_size", "in": "QUERY"},
                             "startFrom": 1, "defaultPageSize": DEP_PRODUCT_PAGE_SIZE,
                             "totalPath": "data.total", "hasMorePath": "data.has_next"},
                 doc_sample={"query": {"page": "1", "page_size": "20"}}),
        Endpoint("GET", "/products/{sku}/batches", batches,
                 operation_id="listProductBatches", summary="查询商品批次（按 SKU）",
                 description="路径参数 sku 依赖 /products 的输出；条数 = (序号 * 7) %% 5，"
                             "取值 0~4，全部商品合计 %d 条；sku 不存在返回 404" % len(ALL_BATCHES),
                 parameters=[pp("sku", description="商品 SKU，来自 /products 的 sku 字段")],
                 record_path="data", primary_key="batch_no",
                 dataset=lambda: list(ALL_BATCHES),
                 doc_sample={"path": {"sku": "DPD-00001"}},
                 note="依赖 /products.sku；expectedTotal 为对全部父记录求和"),
    ]
    return Scenario("depend-path", "路径参数依赖", "前置依赖映射 × PATH；逐父记录条数不同",
                    endpoints, auth=NO_AUTH)


# ==================== depend-body ====================

def _scenario_depend_body():
    def customers(ctx):
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": DEP_CUSTOMERS, "total": len(DEP_CUSTOMERS)}})

    def customer_orders(ctx):
        body = ctx.json_body_or_400()
        customer_id = body.get("customerId")
        if not customer_id:
            raise ApiError(400, "请求体缺少 customerId")
        if not isinstance(customer_id, str):
            raise ApiError(400, "customerId 必须是字符串，收到：%r" % (customer_id,))
        if not any(c["id"] == customer_id for c in DEP_CUSTOMERS):
            return json_resp({"code": 0, "message": "ok", "data": {"items": [], "total": 0}})
        rows = orders_of(customer_id)
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": rows, "total": len(rows)}})

    endpoints = [
        Endpoint("GET", "/customers", customers,
                 operation_id="listCustomers", summary="查询客户列表（依赖父接口）",
                 description="不分页，共 %d 条" % len(DEP_CUSTOMERS),
                 record_path="data.items", primary_key="id",
                 dataset=lambda: list(DEP_CUSTOMERS), doc_sample={}),
        Endpoint("POST", "/customer-orders/query", customer_orders,
                 operation_id="queryCustomerOrders", summary="查询客户订单（按客户 ID）",
                 description="请求体 customerId 依赖 /customers 的 id；条数 = (序号 * 3) %% 4，"
                             "取值 0~3，全部客户合计 %d 条；缺 customerId 返回 400" % len(ALL_ORDERS),
                 request_body=json_body(
                     {"type": "object", "required": ["customerId"],
                      "properties": {"customerId": {"type": "string"}}},
                     {"customerId": "CUS-00001"}),
                 record_path="data.items", primary_key="order_no",
                 dataset=lambda: list(ALL_ORDERS),
                 doc_sample={"body": {"customerId": "CUS-00001"}},
                 note="依赖 /customers.id；缺 customerId 返回 400"),
    ]
    return Scenario("depend-body", "请求体字段依赖", "前置依赖映射 × BODY",
                    endpoints, auth=NO_AUTH)


# ==================== required-header-cookie ====================

RHC_TENANTS = [
    {"id": "RHC-%05d" % i, "name": "租户%d" % i, "region": region, "active": True}
    for i, region in ((1, "cn-east"), (2, "cn-north"), (3, "cn-south"))
]
_RHC_INVOICE_COUNTS = {"RHC-00001": 2, "RHC-00002": 1, "RHC-00003": 3}
_RHC_QUOTA_COUNTS = {"cn-east": 1, "cn-north": 2, "cn-south": 1}


def invoices_of(tenant_id):
    """发票：三个租户依次 2 / 1 / 3 条，合计 6 条；租户不认识返回空列表"""
    seq = _seq_of(tenant_id) or 0
    return [
        {"invoice_no": "%s-INV%d" % (tenant_id, j), "tenant_id": tenant_id,
         "amount": round(100 + seq * 20 + j * 3.5, 2), "paid": (seq + j) % 2 == 0,
         "issued_on": iso(BASE_TIME + timedelta(days=seq * 3 + j))[:10]}
        for j in range(1, _RHC_INVOICE_COUNTS.get(tenant_id, 0) + 1)
    ]


def quotas_of(region):
    """配额：三个区域依次 1 / 2 / 1 条，合计 4 条；区域不认识返回空列表"""
    return [
        {"quota_id": "%s-Q%d" % (region, j), "region": region,
         "resource": ["cpu", "storage"][j - 1], "limit": 100 * j * (len(region) % 5 + 1),
         "used_ratio": round(0.25 * j, 2)}
        for j in range(1, _RHC_QUOTA_COUNTS.get(region, 0) + 1)
    ]


RHC_ALL_INVOICES = [x for t in RHC_TENANTS for x in invoices_of(t["id"])]
RHC_ALL_QUOTAS = [x for t in RHC_TENANTS for x in quotas_of(t["region"])]


def _scenario_required_header_cookie():
    def tenants(ctx):
        return _items(RHC_TENANTS)

    def invoices(ctx):
        tenant_id = ctx.header("X-Tenant-Id")
        if not tenant_id:
            raise ApiError(400, "缺少必填请求头 X-Tenant-Id")
        return _items(invoices_of(tenant_id))

    def quotas(ctx):
        region = ctx.cookies.get("region")
        if not region:
            raise ApiError(400, "缺少必填 Cookie region")
        return _items(quotas_of(region))

    endpoints = [
        Endpoint("GET", "/tenants", tenants,
                 operation_id="listRhcTenants", summary="查询租户列表（前置接口）",
                 description="不分页，共 %d 条；id 给 /invoices 的请求头用，region 给 /quotas 的 Cookie 用"
                             % len(RHC_TENANTS),
                 record_path="data.items", primary_key="id",
                 dataset=lambda: list(RHC_TENANTS), doc_sample={}),
        Endpoint("GET", "/invoices", invoices,
                 operation_id="listRhcInvoices", summary="查询租户发票（必填请求头）",
                 description="请求头 X-Tenant-Id 必填，取自 /tenants 的 id；缺失或为空返回 400；"
                             "三个租户依次 2 / 1 / 3 条，合计 %d 条；不认识的取值返回空列表"
                             % len(RHC_ALL_INVOICES),
                 parameters=[hp("X-Tenant-Id", required=True,
                                description="租户 ID，来自 /tenants 的 id 字段")],
                 record_path="data.items", primary_key="invoice_no",
                 dataset=lambda: list(RHC_ALL_INVOICES),
                 doc_sample={"headers": {"X-Tenant-Id": "RHC-00001"}},
                 note="必填请求头 X-Tenant-Id（文档不给默认值与示例），缺失返回 400；"
                      "依赖 /tenants.id；expectedTotal 为对全部父记录求和",
                 extra_catalog={
                     "requiredTestValues": [{"in": "HEADER", "name": "X-Tenant-Id",
                                             "testValue": "RHC-00001"}],
                     "dependsOn": {"path": "/tenants", "field": "id",
                                   "param": {"name": "X-Tenant-Id", "in": "HEADER"}}}),
        Endpoint("GET", "/quotas", quotas,
                 operation_id="listRhcQuotas", summary="查询区域配额（必填 Cookie）",
                 description="Cookie region 必填，取自 /tenants 的 region；缺失或为空返回 400；"
                             "三个区域依次 1 / 2 / 1 条，合计 %d 条；不认识的取值返回空列表"
                             % len(RHC_ALL_QUOTAS),
                 parameters=[cp("region", required=True,
                                description="区域编码，来自 /tenants 的 region 字段")],
                 record_path="data.items", primary_key="quota_id",
                 dataset=lambda: list(RHC_ALL_QUOTAS),
                 doc_sample={"headers": {"Cookie": "region=cn-east"}},
                 note="必填 Cookie region（文档不给默认值与示例），缺失返回 400；"
                      "依赖 /tenants.region；expectedTotal 为对全部父记录求和",
                 extra_catalog={
                     "requiredTestValues": [{"in": "COOKIE", "name": "region",
                                             "testValue": "cn-east"}],
                     "dependsOn": {"path": "/tenants", "field": "region",
                                   "param": {"name": "region", "in": "COOKIE"}}}),
    ]
    return Scenario("required-header-cookie", "必填的请求头参数与 Cookie 参数",
                    "业务参数位置 HEADER / COOKIE 且必填、无默认值；可绑定到前置接口的字段",
                    endpoints, auth=NO_AUTH,
                    description="前置接口 /tenants；/invoices 需要必填请求头 X-Tenant-Id，"
                                "/quotas 需要必填 Cookie region，缺失都返回 400")


def _items(rows):
    return json_resp({"code": 0, "message": "ok", "data": {"items": rows, "total": len(rows)}})


# ==================== depend-empty-parent ====================

DEP_EMPTY_CATEGORY_IDS = ["DEP-CAT-%05d" % i for i in (1, 2, 3)]
# 只用于文档示例，说明父记录长什么样；真实响应恒为 0 条
_DEP_EMPTY_SAMPLE_CATEGORY = {"id": DEP_EMPTY_CATEGORY_IDS[0], "name": "示例分类"}
DEP_EMPTY_PRODUCTS_PER_CATEGORY = 2


def category_products_of(category_id):
    seq = _seq_of(category_id)
    return [
        {"product_id": "%s-P%d" % (category_id, j), "category_id": category_id,
         "name": "分类%d商品%d" % (seq, j), "price": round(10 + seq * 5 + j * 0.75, 2),
         "on_sale": (seq + j) % 2 == 1}
        for j in range(1, DEP_EMPTY_PRODUCTS_PER_CATEGORY + 1)
    ]


def _scenario_depend_empty_parent():
    def categories(ctx):
        return _items([])

    def products(ctx):
        category_id = ctx.path_params.get("categoryId")
        if category_id not in DEP_EMPTY_CATEGORY_IDS:
            return json_resp({"code": 404, "message": "分类不存在：%s" % category_id}, status=404)
        return _items(category_products_of(category_id))

    category_schema = {"type": "object", "properties": {
        "id": {"type": "string"}, "name": {"type": "string"}}}
    endpoints = [
        Endpoint("GET", "/categories", categories,
                 operation_id="listEmptyCategories", summary="查询分类列表（前置接口，恒定 0 条）",
                 description="恒定返回空数组（data.items 为 []、data.total 为 0）。"
                             "记录结构在 schema 里显式声明（id、name）；下面的响应示例只为示意字段，"
                             "真实响应里没有任何记录",
                 record_path="data.items", primary_key="id",
                 dataset=lambda: [], doc_sample=None,
                 # 空数组推断不出记录字段，所以这里显式声明；示例带一条记录只为示意字段
                 response_schema={"type": "object", "properties": {
                     "code": {"type": "integer"}, "message": {"type": "string"},
                     "data": {"type": "object", "properties": {
                         "items": {"type": "array", "items": category_schema},
                         "total": {"type": "integer"}}}}},
                 response_example={"code": 0, "message": "ok",
                                   "data": {"items": [_DEP_EMPTY_SAMPLE_CATEGORY], "total": 1}},
                 note="恒定 0 条；文档示例里的那条记录只为示意字段，真实响应没有"),
        Endpoint("GET", "/categories/{categoryId}/products", products,
                 operation_id="listEmptyCategoryProducts", summary="查询分类商品（依赖 /categories）",
                 description="路径参数 categoryId 取自 /categories 的 id。前置接口恒定 0 条，"
                             "所以按依赖展开不会产生任何调用；直接带合法取值（%s）调用时"
                             "每个返回 %d 条，其它取值返回 404"
                             % (" / ".join(DEP_EMPTY_CATEGORY_IDS), DEP_EMPTY_PRODUCTS_PER_CATEGORY),
                 parameters=[pp("categoryId", description="分类 ID，来自 /categories 的 id 字段")],
                 record_path="data.items", primary_key="product_id",
                 expected_total=0,
                 doc_sample={"path": {"categoryId": DEP_EMPTY_CATEGORY_IDS[0]}},
                 note="依赖 /categories.id；前置 0 条，按依赖展开 expectedTotal 为 0；"
                      "直接带合法取值调用返回 %d 条" % DEP_EMPTY_PRODUCTS_PER_CATEGORY,
                 extra_catalog={
                     "validPathValues": {"categoryId": list(DEP_EMPTY_CATEGORY_IDS)},
                     "directCallTotal": DEP_EMPTY_PRODUCTS_PER_CATEGORY,
                     "dependsOn": {"path": "/categories", "field": "id",
                                   "param": {"name": "categoryId", "in": "PATH"}}}),
    ]
    return Scenario("depend-empty-parent", "前置接口返回 0 条",
                    "依赖展开时前置接口成功但没有记录；下游直接带合法取值仍可正常调用",
                    endpoints, auth=NO_AUTH)


# ==================== depend-mixed-key ====================

# 取值字段 ref：数字 1 与文本 "1" 是同一个取值的两种类型（故意的）
MXK_SHOPS = [
    {"id": "MXK-%05d" % i, "name": "门店%d" % i, "ref": ref, "opened": i % 2 == 1}
    for i, ref in ((1, 1), (2, "1"), (3, 2), (4, "3"))
]
MXK_SEASONS = [
    {"code": "S%d" % i, "name": "第 %d 季" % i, "seq": i} for i in (1, 2)
]
_MXK_STAFF_COUNTS = {"1": 2, "2": 1, "3": 2}


def staff_of(ref):
    """按取值（文本形式）返回员工：`1` → 2 条，`2` → 1 条，`3` → 2 条；其它返回 None"""
    count = _MXK_STAFF_COUNTS.get(ref)
    if count is None:
        return None
    return [
        {"staff_no": "MXK-REF%s-ST%d" % (ref, j), "shop_ref": ref,
         "name": "店员%s-%d" % (ref, j), "level": (int(ref) + j) % 3 + 1}
        for j in range(1, count + 1)
    ]


def sales_of(shop_ref, season):
    """每个（取值, 季）组合 1 条；任一取值不认识返回空列表"""
    if shop_ref not in _MXK_STAFF_COUNTS or not any(s["code"] == season for s in MXK_SEASONS):
        return []
    return [{"sale_id": "MXK-REF%s-%s" % (shop_ref, season), "shop_ref": shop_ref,
             "season": season, "amount": round(int(shop_ref) * 1000 + int(season[1:]) * 12.5, 2)}]


MXK_DISTINCT_REFS = sorted(_MXK_STAFF_COUNTS)
MXK_STAFF_DISTINCT = [x for ref in MXK_DISTINCT_REFS for x in staff_of(ref)]
MXK_SALES_DISTINCT = [x for ref in MXK_DISTINCT_REFS for s in MXK_SEASONS
                      for x in sales_of(ref, s["code"])]


def _scenario_depend_mixed_key():
    def shops(ctx):
        return _items(MXK_SHOPS)

    def seasons(ctx):
        return _items(MXK_SEASONS)

    def staff(ctx):
        ref = ctx.path_params.get("ref")
        rows = staff_of(ref)
        if rows is None:
            return json_resp({"code": 404, "message": "门店取值不存在：%s" % ref}, status=404)
        return _items(rows)

    def sales(ctx):
        shop_ref, season = ctx.query.get("shop_ref"), ctx.query.get("season")
        if not shop_ref:
            raise ApiError(400, "缺少必填参数 shop_ref")
        if not season:
            raise ApiError(400, "缺少必填参数 season")
        return _items(sales_of(shop_ref, season))

    refs_text = "、".join(json.dumps(s["ref"]) for s in MXK_SHOPS)
    per_parent_staff = sum(_MXK_STAFF_COUNTS[str(s["ref"])] for s in MXK_SHOPS)
    # ref 的类型是混的，推断只会留下先出现的整数类型，所以 /shops 的 schema 显式声明
    shop_schema = {"type": "object", "properties": {
        "id": {"type": "string"}, "name": {"type": "string"},
        "ref": {"oneOf": [{"type": "integer"}, {"type": "string"}],
                "description": "取值字段：有的记录是数字、有的是文本"},
        "opened": {"type": "boolean"}}}
    endpoints = [
        Endpoint("GET", "/shops", shops,
                 operation_id="listMixedKeyShops", summary="查询门店列表（前置一，取值字段类型混用）",
                 description="不分页，共 %d 条。取值字段 ref 依次为 %s：数字 1 与文本 \"1\" 是"
                             "同一个取值的两种类型" % (len(MXK_SHOPS), refs_text),
                 record_path="data.items", primary_key="id",
                 dataset=lambda: list(MXK_SHOPS), doc_sample=None,
                 response_schema={"type": "object", "properties": {
                     "code": {"type": "integer"}, "message": {"type": "string"},
                     "data": {"type": "object", "properties": {
                         "items": {"type": "array", "items": shop_schema},
                         "total": {"type": "integer"}}}}},
                 response_example={"code": 0, "message": "ok",
                                   "data": {"items": MXK_SHOPS[:2], "total": len(MXK_SHOPS)}},
                 note="ref 依次为 %s；文档里 ref 声明为 oneOf 整数 / 文本" % refs_text,
                 extra_catalog={"mixedTypeField": {
                     "field": "ref",
                     "values": [{"id": s["id"], "value": s["ref"],
                                 "jsonType": "integer" if isinstance(s["ref"], int) else "string"}
                                for s in MXK_SHOPS]}}),
        Endpoint("GET", "/shops/{ref}/staff", staff,
                 operation_id="listMixedKeyStaff", summary="查询门店员工（按取值字段）",
                 description="路径参数 ref 取自 /shops 的 ref。取值 1 返回 2 条、2 返回 1 条、3 返回 2 条，"
                             "其它取值返回 404。路径里数字 1 与文本 \"1\" 是同一个地址，返回相同的 2 条",
                 parameters=[pp("ref", description="门店取值，来自 /shops 的 ref 字段")],
                 record_path="data.items", primary_key="staff_no",
                 dataset=lambda: list(MXK_STAFF_DISTINCT),
                 doc_sample={"path": {"ref": "1"}},
                 note="依赖 /shops.ref；服务按取值返回：1 → 2 条、2 → 1 条、3 → 2 条。"
                      "expectedTotal %d 是 3 个不同取值各调一次的合计（参考值）；"
                      "若按 4 条父记录各调一次，共返回 %d 条，其中取值 1 的 2 条主键重复"
                      % (len(MXK_STAFF_DISTINCT), per_parent_staff),
                 extra_catalog={
                     "rowsByParamValue": dict(_MXK_STAFF_COUNTS),
                     "referenceTotals": {"oneCallPerDistinctValue": len(MXK_STAFF_DISTINCT),
                                         "oneCallPerParentRecord": per_parent_staff},
                     "dependsOn": {"path": "/shops", "field": "ref",
                                   "param": {"name": "ref", "in": "PATH"}}}),
        Endpoint("GET", "/seasons", seasons,
                 operation_id="listMixedKeySeasons", summary="查询季度列表（前置二）",
                 description="不分页，共 %d 条；code 是文本，另有整数字段 seq" % len(MXK_SEASONS),
                 record_path="data.items", primary_key="code",
                 dataset=lambda: list(MXK_SEASONS), doc_sample={}),
        Endpoint("GET", "/sales", sales,
                 operation_id="listMixedKeySales", summary="查询销售额（两个参数取自两个前置接口）",
                 description="查询参数 shop_ref 取自 /shops 的 ref，season 取自 /seasons 的 code，"
                             "两个都必填，缺任一个返回 400；每个（取值, 季）组合返回 1 条，"
                             "取值不认识返回空列表",
                 parameters=[qp("shop_ref", required=True,
                                description="门店取值，来自 /shops 的 ref 字段"),
                             qp("season", required=True,
                                description="季度编码，来自 /seasons 的 code 字段")],
                 record_path="data.items", primary_key="sale_id",
                 dataset=lambda: list(MXK_SALES_DISTINCT),
                 doc_sample={"query": {"shop_ref": "1", "season": "S1"}},
                 note="每个（shop_ref, season）组合返回 1 条；expectedTotal %d 只是参考值："
                      "按 3 个不同取值与 %d 个季度两两组合各调一次算出，后端实际怎么组合不在这里规定"
                      % (len(MXK_SALES_DISTINCT), len(MXK_SEASONS)),
                 extra_catalog={
                     "rowsPerCombination": 1,
                     "referenceTotalIfAllCombinations": len(MXK_SALES_DISTINCT),
                     "dependsOn": [
                         {"path": "/shops", "field": "ref",
                          "param": {"name": "shop_ref", "in": "QUERY"}},
                         {"path": "/seasons", "field": "code",
                          "param": {"name": "season", "in": "QUERY"}},
                     ]}),
    ]
    return Scenario("depend-mixed-key", "取值字段数字与文本混用",
                    "前置接口同一个取值字段有的记录是数字、有的是文本；只差类型的取值如何发起下游请求；"
                    "一个下游的两个参数分别取自两个前置接口",
                    endpoints, auth=NO_AUTH)


def build_scenarios():
    return [_scenario_depend_path(), _scenario_depend_body(),
            _scenario_required_header_cookie(), _scenario_depend_empty_parent(),
            _scenario_depend_mixed_key()]
