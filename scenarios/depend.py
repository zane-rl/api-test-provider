# -*- coding: utf-8 -*-
"""D 类：依赖场景（均无鉴权）。

父接口提供记录，子接口按父记录的字段取数：
- `depend-path`：子接口通过**路径参数**依赖父记录的 sku
- `depend-body`：子接口通过**请求体字段**依赖父记录的 customerId

子接口每个父记录的条数刻意不同（公式见下方注释与 README），用于验证依赖展开后逐父取数。
"""

from datetime import timedelta

from .common import (
    Endpoint, Scenario, NO_AUTH, ApiError,
    build_dataset, iso, BASE_TIME, json_resp, qp, json_body, pp,
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


def build_scenarios():
    return [_scenario_depend_path(), _scenario_depend_body()]
