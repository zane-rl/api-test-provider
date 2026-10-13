# -*- coding: utf-8 -*-
"""C 类：请求与响应场景（除 required-business-param / dotted-param 用固定 API Key 外，均无鉴权）。

覆盖：Query / 请求体原样回显（验证 `+`、中文、空格的编码）、记录路径命中单对象、
业务成功码的多种写法、一个接口产出多个对象（嵌套数组子表）、
验证接口带「无来源的必填参数」（路径参数 + required Query）、参数名带点号、
公共参数（components.parameters + $ref）、根对象作为一条记录、表单请求体的业务 POST、
两层嵌套数组、特殊字段名（连字符 / 中文 / 数字开头）。
"""

from datetime import timedelta

from .auth import API_KEY
from .common import (
    Endpoint, Scenario, NO_AUTH, AuthSpec, ApiError,
    api_key_req, build_dataset, build_record, iso, BASE_TIME,
    json_resp, qp, hp, pp, ref_param, json_body, form_body, json_type_of,
    require_media_type, FORM_MEDIA_TYPE,
)


# ==================== echo：请求原样回显 ====================

def _scenario_echo():
    def echo_query(ctx):
        query = ctx.query
        record = {
            "id": "ECHO-00001",
            # 服务端解码后的取值：`+` 若未被编码为 %2B，这里会变成空格，一眼可见
            "since": query.get("since"),
            "q": query.get("q"),
            "received_query": query,
            "raw_query": ctx.raw_query,
            "param_count": len(query),
        }
        return json_resp({"code": 0, "message": "ok", "data": {"items": [record]}})

    def echo_body(ctx):
        body = ctx.json_body_or_400()
        record = {
            "id": "ECHOBODY-00001",
            "received_body": body,
            "raw_body": ctx.body_raw,
            "types": {k: json_type_of(v) for k, v in body.items()},
        }
        return json_resp({"code": 0, "message": "ok", "data": {"items": [record]}})

    endpoints = [
        Endpoint("GET", "/echo", echo_query,
                 operation_id="listEchoedQuery", summary="回显收到的查询参数",
                 description="把服务端解码后收到的每个 Query 值原样放进唯一一条记录，"
                             "用于验证 `+` 被编码为 %2B、中文与空格正确",
                 parameters=[qp("since", "string", description="带时区的时间，例 2026-09-10T14:30:00+08:00"),
                             qp("q", "string", description="任意文本，可含中文与空格")],
                 record_path="data.items", primary_key="id", expected_total=1,
                 doc_sample={"query": {"since": "2026-09-10T14:30:00+08:00", "q": "中文 关键词"}}),
        Endpoint("POST", "/echo-body", echo_body,
                 operation_id="listEchoedBody", summary="回显收到的请求体",
                 description="把收到的 JSON 体原样作为一条记录返回，并附每个顶层值的 JSON 类型",
                 request_body=json_body({"type": "object", "additionalProperties": True},
                                        {"page": 1, "size": 20, "active": True,
                                         "keyword": "中文 关键词", "extra": None}),
                 record_path="data.items", primary_key="id", expected_total=1,
                 doc_sample={"body": {"page": 1, "size": 20, "active": True,
                                      "keyword": "中文 关键词", "extra": None}}),
    ]
    return Scenario("echo", "请求回显", "Query / 请求体编码与类型保真验证", endpoints, auth=NO_AUTH)


# ==================== single-object：记录路径命中单对象 ====================

PROFILE = {
    "id": "PROFILE-00001",
    "tenant": "t-001",
    "display_name": "测试租户档案",
    "quota": 5000,
    "usage_ratio": 0.375,
    "enabled": True,
    "created_at": iso(BASE_TIME),
    "expired_at": None,
    "owner": {"id": "OP-01", "name": "操作员1", "email": "op1@example.test"},
}


def _scenario_single_object():
    def handler(ctx):
        return json_resp({"code": 0, "message": "ok", "data": PROFILE})

    ep = Endpoint(
        "GET", "/profile", handler,
        operation_id="getProfile", summary="查询租户档案（单个对象）",
        description="记录路径命中单个对象，应产生 1 条记录",
        record_path="data", primary_key="id", dataset=lambda: [PROFILE],
        doc_sample={},
    )
    return Scenario("single-object", "记录路径命中单对象", "记录路径 × 单对象 → 1 条记录",
                    [ep], auth=NO_AUTH)


# ==================== biz-code：业务成功条件 ====================

BIZ_ROWS = build_dataset("BIZ", 3)


def _scenario_biz_code():
    def ok_zero(ctx):
        return json_resp({"code": 0, "message": "ok", "data": {"items": BIZ_ROWS}})

    def ok_200(ctx):
        return json_resp({"code": 200, "message": "ok", "data": {"items": BIZ_ROWS}})

    def ok_string(ctx):
        return json_resp({"code": "0", "message": "ok", "data": {"items": BIZ_ROWS}})

    def biz_fail(ctx):
        return json_resp({"code": 5001, "message": "库存服务繁忙", "data": None})

    def make(path, handler, op, summary, desc, total, sample_note=""):
        return Endpoint("GET", path, handler, operation_id=op, summary=summary,
                        description=desc, record_path="data.items", primary_key="id",
                        dataset=(lambda: list(BIZ_ROWS)) if total else None,
                        expected_total=None if total else 0,
                        doc_sample={}, note=sample_note)

    endpoints = [
        make("/ok-zero", ok_zero, "listOkZero", "查询数据（业务码 0）",
             "HTTP 200 且 code 为数值 0，业务成功", True),
        make("/ok-200", ok_200, "listOk200", "查询数据（业务码 200）",
             "HTTP 200 且 code 为数值 200，用于验证成功条件的多个允许值", True),
        make("/ok-string", ok_string, "listOkString", "查询数据（业务码字符串 \"0\"）",
             "code 是字符串 \"0\"，用于验证按类型匹配", True),
        make("/biz-fail", biz_fail, "listBizFail", "查询数据（业务失败）",
             "HTTP 200 但 code 为 5001，应判业务失败", False,
             "HTTP 200 业务失败，期望后端判失败"),
    ]
    return Scenario("biz-code", "业务成功条件", "成功条件 EQ/NE × 数值/字符串 × 多允许值",
                    endpoints, auth=NO_AUTH)


# ==================== nested：一接口多对象 ====================

def _build_orders():
    orders = []
    for i in range(1, 13):
        line_count = (i % 3) + 1          # 每单 1~3 行，条数可预期
        orders.append({
            "order_no": "ORD-%05d" % i,
            "status": ["NEW", "PAID", "SHIPPED"][i % 3],
            "amount": round(100 + i * 13.7, 2),
            "urgent": i % 4 == 0,
            "created_at": iso(BASE_TIME + timedelta(days=i)),
            "cancelled_at": None if i % 5 else iso(BASE_TIME + timedelta(days=i, hours=3)),
            "customer": {"id": "CUS-%05d" % ((i % 6) + 1),
                         "name": "客户%d" % ((i % 6) + 1),
                         "level": ["VIP", "NORMAL"][i % 2]},
            "lines": [{"line_no": j,
                       "sku": "PRD-%05d" % ((i * 3 + j) % 57 + 1),
                       "quantity": j * 2 + i % 4,
                       "price": round(9.9 + (i + j) * 1.37, 2)}
                      for j in range(1, line_count + 1)],
        })
    return orders


ORDERS = _build_orders()
ORDER_LINES = [dict(line, order_no=o["order_no"]) for o in ORDERS for line in o["lines"]]


def _scenario_nested():
    def handler(ctx):
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": ORDERS, "total": len(ORDERS)}})

    ep = Endpoint(
        "GET", "/orders", handler,
        operation_id="listOrders", summary="查询订单列表（含嵌套子表）",
        description="每条订单含对象字段 customer 与嵌套数组 lines（每单 1~3 行），"
                    "用于验证一个接口产出多个数据对象",
        record_path="data.items", primary_key="order_no", dataset=lambda: list(ORDERS),
        pagination={"type": "NONE"}, doc_sample={},
        note="子表 data.items[].lines 共 %d 行（主键 order_no + line_no）" % len(ORDER_LINES),
    )
    return Scenario("nested", "嵌套对象与子表", "对象字段展开 + 嵌套数组子表（子表复制父字段）",
                    [ep], auth=NO_AUTH)


# ==================== required-business-param：验证接口带无来源的必填参数 ====================

# 可查到的客户：只认这几个 id，未知 id 返回 404，用来证明路径参数的值确实被带上了
RBP_CUSTOMERS = build_dataset("RBP-CUS", 5)
RBP_SAMPLE_TENANT = "tenant-001"


def _rbp_record(customer, tenant):
    """单条客户档案：tenant 原样回显，E2E 可据此断言补填的测试值确实送达"""
    record = dict(customer)
    record["tenant"] = tenant
    return record


def _scenario_required_business_param():
    def handler(ctx):
        tenant = ctx.query.get("tenant")
        if not tenant:
            raise ApiError(400, "缺少必填参数 tenant（Query）")
        customer_id = ctx.path_params.get("customerId")
        customer = next((c for c in RBP_CUSTOMERS if c["id"] == customer_id), None)
        if customer is None:
            raise ApiError(404, "客户不存在：%s" % customer_id)
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": [_rbp_record(customer, tenant)]}})

    ep = Endpoint(
        "GET", "/customers/{customerId}", handler,
        operation_id="getCustomerProfile", summary="查询客户档案（按客户 ID 与租户）",
        description="路径参数 customerId 与必填 Query 参数 tenant 都没有默认值、也没有样例值，"
                    "且不来自任何前置接口；缺 tenant 返回 400，customerId 不存在返回 404。"
                    "可用 customerId：%s ~ %s；tenant 任意非空值，原样回显在记录里"
                    % (RBP_CUSTOMERS[0]["id"], RBP_CUSTOMERS[-1]["id"]),
        # 两个参数都刻意不给 example / default，避免被后端当成固定值
        parameters=[pp("customerId", description="客户 ID"),
                    qp("tenant", "string", required=True, description="租户编码")],
        record_path="data.items", primary_key="id",
        dataset=lambda: [_rbp_record(RBP_CUSTOMERS[0], RBP_SAMPLE_TENANT)],
        doc_sample={"path": {"customerId": RBP_CUSTOMERS[0]["id"]},
                    "query": {"tenant": RBP_SAMPLE_TENANT}},
        note="验证接口必填参数无来源：缺值时后端应返回 NEEDS_TEST_VALUES 且不发请求；"
             "若误发请求（不带 tenant）会收到 400",
        extra_catalog={"requiredTestValues": [
            {"in": "PATH", "name": "customerId", "testValue": RBP_CUSTOMERS[0]["id"]},
            {"in": "QUERY", "name": "tenant", "testValue": RBP_SAMPLE_TENANT},
        ]},
    )
    return Scenario("required-business-param", "验证接口带无来源的必填参数",
                    "验证连接前检查请求值是否齐全：路径参数与 required Query 无固定值 / 无依赖 / 无测试值时"
                    "返回 NEEDS_TEST_VALUES 且不发请求",
                    [ep], auth=AuthSpec(kind="apiKey",
                                        requirements=[api_key_req("header", "X-Api-Key", API_KEY)]))


# ==================== dotted-param：参数名带点号 ====================

DOTTED_ROWS = build_dataset("DOT", 3)
DOTTED_SAMPLE_FILTER = "dotted-name-001"


def _dotted_records(filter_name, page_size):
    """每条记录回显收到的 filter.name / page.size，E2E 据此断言点号参数名被原样送达"""
    return [dict(row, filterName=filter_name, pageSize=page_size) for row in DOTTED_ROWS]


def _scenario_dotted_param():
    def handler(ctx):
        filter_name = ctx.query.get("filter.name")
        if not filter_name:
            raise ApiError(400, "缺少必填参数 filter.name（Query）")
        page_size = ctx.int_query("page.size", minimum=1)
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": _dotted_records(filter_name, page_size)}})

    ep = Endpoint(
        "GET", "/items", handler,
        operation_id="listDottedParamItems", summary="查询数据（参数名带点号）",
        description="Query 参数名本身带点号：必填 filter.name、可选整数 page.size，都没有默认值与样例值。"
                    "缺少或为空的 filter.name 返回 400；page.size 不是正整数返回 400。"
                    "每条记录把收到的 filter.name 回显在 filterName、page.size 回显在 pageSize（未传为 null）",
        # 两个参数都刻意不给 example / default，避免被后端当成固定值
        parameters=[qp("filter.name", "string", required=True, description="按名称过滤"),
                    qp("page.size", "integer", description="每页数量（只回显，不影响返回条数）")],
        record_path="data.items", primary_key="id",
        dataset=lambda: _dotted_records(DOTTED_SAMPLE_FILTER, None),
        doc_sample={"query": {"filter.name": DOTTED_SAMPLE_FILTER, "page.size": "20"}},
        note="参数名带点号：后端不应把 filter.name 拆成嵌套结构；缺 filter.name 会收到 400",
        extra_catalog={"requiredTestValues": [
            {"in": "QUERY", "name": "filter.name", "testValue": DOTTED_SAMPLE_FILTER},
        ]},
    )
    return Scenario("dotted-param", "参数名带点号",
                    "Query 参数名含点号（filter.name / page.size）时按原名透传，不拆成嵌套对象",
                    [ep], auth=AuthSpec(kind="apiKey",
                                        requirements=[api_key_req("header", "X-Api-Key", API_KEY)]))


# ==================== shared-params：公共参数（components.parameters + $ref） ====================

SP_LOCALES = ["zh-CN", "en-US"]
# 公共参数的测试值；键是 components.parameters 里的组件名
SP_TEST_VALUES = {"TenantId": "tenant-001", "Locale": "zh-CN", "ApiVersion": "2"}
# 组件名 -> (位置, 参数名, 回显到记录里的字段名)
SP_COMPONENTS = {
    "TenantId": ("HEADER", "X-Tenant-Id", "tenantId"),
    "Locale": ("QUERY", "locale", "locale"),
    "ApiVersion": ("QUERY", "apiVersion", "apiVersion"),
}


def _sp_shared_parameters():
    """文档 components.parameters 的内容：三个参数都必填，刻意不给 example / default"""
    locale = qp("locale", "string", required=True, description="语言（公共参数）")
    locale["schema"]["enum"] = list(SP_LOCALES)
    return {
        "TenantId": hp("X-Tenant-Id", "string", required=True, description="租户 ID（公共参数）"),
        "Locale": locale,
        "ApiVersion": qp("apiVersion", "integer", required=True,
                         description="接口版本号（公共参数）"),
    }


def _sp_read(ctx, components):
    """只校验该端点引用了的公共参数，返回要回显的 {tenantId, locale, apiVersion}（未引用为 None）"""
    values = {"tenantId": None, "locale": None, "apiVersion": None}
    if "TenantId" in components:
        tenant = ctx.header("X-Tenant-Id")
        if not tenant:
            raise ApiError(400, "缺少公共参数 X-Tenant-Id（Header）")
        values["tenantId"] = tenant
    if "Locale" in components:
        locale = ctx.query.get("locale")
        if not locale:
            raise ApiError(400, "缺少公共参数 locale（Query）")
        if locale not in SP_LOCALES:
            raise ApiError(400, "参数 locale 取值不在枚举内：%r，可选 %s"
                           % (locale, " / ".join(SP_LOCALES)))
        values["locale"] = locale
    if "ApiVersion" in components:
        if not ctx.query.get("apiVersion"):
            raise ApiError(400, "缺少公共参数 apiVersion（Query）")
        values["apiVersion"] = ctx.int_query("apiVersion")
    return values


def _sp_endpoint(path, operation_id, summary, description, prefix, components=(),
                 ref_location=None, keyword=False, inline_locale=False):
    """一个 shared-params 端点。

    components   ：引用的公共参数（组件名）
    ref_location ：引用写在哪里——operation（操作级）/ pathItem（路径项级）/ None（不引用）
    keyword      ：是否内联声明可选的 keyword（多个接口重复出现，但不走公共定义）
    inline_locale：是否内联声明一个与公共参数同名的 locale（可选、无 enum、不校验）
    """
    rows = build_dataset(prefix, 3)

    def records(values, keyword_value=None):
        return [dict(row, **values) for row in rows
                if not keyword_value or keyword_value in row["name"]]

    def handler(ctx):
        values = _sp_read(ctx, components)
        if inline_locale:
            values["locale"] = ctx.query.get("locale")      # 自用参数：原样回显，不做任何校验
        keyword_value = ctx.query.get("keyword") if keyword else None
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": records(values, keyword_value)}})

    refs = [ref_param(name) for name in components]
    inline = []
    if keyword:
        inline.append(qp("keyword", "string",
                         description="按名称包含过滤，可不传（本接口内联声明，不是公共参数）"))
    if inline_locale:
        inline.append(qp("locale", "string",
                         description="本接口自用的展示语言，任意字符串、可不传；"
                                     "与公共参数 locale 同名但没有走公共定义，没有枚举限制"))

    sample = {"tenantId": None, "locale": None, "apiVersion": None}
    sample_query, sample_headers, test_values = {}, {}, []
    for name in components:
        where, param_name, field = SP_COMPONENTS[name]
        value = SP_TEST_VALUES[name]
        sample[field] = int(value) if name == "ApiVersion" else value
        (sample_headers if where == "HEADER" else sample_query)[param_name] = value
        test_values.append({"in": where, "name": param_name, "testValue": value})

    return Endpoint(
        "GET", path, handler, operation_id=operation_id, summary=summary,
        description=description,
        parameters=(refs if ref_location == "operation" else []) + inline,
        path_parameters=refs if ref_location == "pathItem" else [],
        record_path="data.items", primary_key="id",
        dataset=lambda: records(sample),
        doc_sample={"query": sample_query, "headers": sample_headers},
        extra_catalog={
            "requiredTestValues": test_values,
            "sharedParameters": {
                "refLocation": ref_location,
                "refs": [{"component": name, "in": SP_COMPONENTS[name][0],
                          "name": SP_COMPONENTS[name][1]} for name in components],
                "inlineParameters": [{"in": "QUERY", "name": p["name"]} for p in inline],
            },
        },
    )


def _scenario_shared_params():
    all_three = ("TenantId", "Locale", "ApiVersion")
    echo = "每条记录回显收到的 tenantId / locale / apiVersion"
    endpoints = [
        _sp_endpoint("/orders", "listSharedParamOrders", "查询订单（操作级引用全部公共参数）",
                     "在操作级用 $ref 引用全部 3 个公共参数；另内联声明可选的 keyword。"
                     "公共参数缺失或 locale 不在枚举内返回 400。" + echo,
                     "SP-ORD", all_three, "operation", keyword=True),
        _sp_endpoint("/customers", "listSharedParamCustomers", "查询客户（路径项级引用全部公共参数）",
                     "3 个公共参数写在路径项（path item）的 parameters 里用 $ref 引用，操作级只有内联的 keyword。"
                     "公共参数缺失或 locale 不在枚举内返回 400。" + echo,
                     "SP-CUS", all_three, "pathItem", keyword=True),
        _sp_endpoint("/products", "listSharedParamProducts", "查询商品（只引用公共参数 locale）",
                     "只引用公共参数 Locale；不需要 X-Tenant-Id 与 apiVersion（记录里这两项恒为 null）。"
                     "locale 缺失或不在枚举内返回 400",
                     "SP-PRD", ("Locale",), "operation"),
        _sp_endpoint("/warehouses", "listSharedParamWarehouses", "查询仓库（不引用任何公共参数）",
                     "不引用任何公共参数；内联声明的 locale 与公共参数同名，但可选、无枚举、服务端不校验，"
                     "任意值或不传都返回 200，收到的值原样回显在记录的 locale 里",
                     "SP-WH", inline_locale=True),
    ]
    return Scenario(
        "shared-params", "公共参数（components.parameters）",
        "公共参数识别：components.parameters + $ref（操作级 / 路径项级）；"
        "内联的同名或重复参数不应被当成公共参数",
        endpoints, auth=NO_AUTH, shared_parameters=_sp_shared_parameters(),
        description="公共参数定义在 components.parameters：X-Tenant-Id（Header）、locale（Query，枚举 %s）、"
                    "apiVersion（Query，整数），均必填。/orders 在操作级引用全部，/customers 在路径项级引用全部，"
                    "/products 只引用 locale，/warehouses 不引用。"
                    "/orders 与 /customers 各自内联的 keyword、/warehouses 内联的 locale 都不是公共参数"
                    % " / ".join(SP_LOCALES))


# ==================== root-object-record：根对象作为一条记录 ====================

ROOT_ORDER = {
    "orderNo": "ROOT-ORD-00001",
    "status": "PAID",
    "amount": 237.5,
    "urgent": False,
    "createdAt": iso(BASE_TIME),
    "remark": None,
    "tags": ["a", "b"],
    "lines": [{"lineNo": j, "sku": "PRD-%05d" % j, "quantity": j + 1,
               "price": round(9.9 + j * 1.37, 2)} for j in (1, 2)],
}


def _scenario_root_object_record():
    def handler(ctx):
        return json_resp(ROOT_ORDER)

    ep = Endpoint(
        "GET", "/order", handler,
        operation_id="getRootOrder", summary="查询单个订单（根对象）",
        description="响应的根就是一个对象，没有 code / data 包裹层；根对象里同时有标量字段、"
                    "标量数组 tags 与对象数组 lines。应把根对象当作 1 条记录，数组保留为字段",
        record_path="", primary_key="orderNo", dataset=lambda: [ROOT_ORDER],
        doc_sample={},
        note="根对象即一条记录：tags / lines 应保留为该记录的数组字段，不应被展开成多条记录",
        extra_catalog={"arrayFields": {"tags": "SCALAR_ARRAY", "lines": "OBJECT_ARRAY"}},
    )
    return Scenario("root-object-record", "根对象作为一条记录",
                    "记录路径 = 根对象 → 1 条记录；标量数组与对象数组保留为字段",
                    [ep], auth=NO_AUTH)


# ==================== form-body-post：表单请求体的业务 POST ====================

FBP_STATUSES = ["NEW", "PAID", "SHIPPED"]
FBP_ROWS = [dict(build_record("FBP", i), status=FBP_STATUSES[i % len(FBP_STATUSES)])
            for i in range(1, 7)]


def _scenario_form_body_post():
    def handler(ctx):
        require_media_type(ctx, FORM_MEDIA_TYPE)
        form = ctx.form
        status = form.get("status")
        if status and status not in FBP_STATUSES:
            raise ApiError(400, "参数 status 取值不在枚举内：%r，可选 %s"
                           % (status, " / ".join(FBP_STATUSES)))
        keyword = form.get("keyword")
        rows = [r for r in FBP_ROWS
                if (not status or r["status"] == status)
                and (not keyword or keyword in r["name"])]
        return json_resp({"code": 0, "message": "ok", "data": {"items": rows}})

    ep = Endpoint(
        "POST", "/orders/search", handler,
        operation_id="searchOrdersByForm", summary="按条件查询订单（表单请求体）",
        description="请求体只接受 %s（字段 keyword / status 均可选，都不传返回全部 %d 条）；"
                    "用 JSON 或其它 Content-Type 提交返回 415" % (FORM_MEDIA_TYPE, len(FBP_ROWS)),
        request_body=form_body({"type": "object", "properties": {
            "keyword": {"type": "string", "description": "按名称包含过滤"},
            "status": {"type": "string", "enum": list(FBP_STATUSES)}}}),
        record_path="data.items", primary_key="id", dataset=lambda: list(FBP_ROWS),
        doc_sample={"body": "", "headers": {"Content-Type": FORM_MEDIA_TYPE}},
        note="请求体为表单的业务接口：后端应标记为不支持；误用 JSON 提交会收到 415",
        extra_catalog={"expectedBackendSupport": "UNSUPPORTED",
                       "requestContentType": FORM_MEDIA_TYPE},
    )
    return Scenario("form-body-post", "表单请求体的业务 POST",
                    "业务接口请求体为 application/x-www-form-urlencoded → 应标记为不支持",
                    [ep], auth=NO_AUTH)


# ==================== nested-two-level：两层嵌套数组 ====================

_NTL_LINE_COUNTS = {1: 2, 2: 3, 3: 1}


def _ntl_batches(line_no, i, j):
    """批次条数公式：`(订单序号 + 行号) % 2 + 1`，取值 1~2 条"""
    return [
        {"batch_no": "%s-B%d" % (line_no, k), "qty": i + j + k,
         "produced_on": iso(BASE_TIME + timedelta(days=i * 10 + j + k))[:10]}
        for k in range(1, (i + j) % 2 + 2)
    ]


def _ntl_order(i):
    order_no = "NTL-%05d" % i
    lines = []
    for j in range(1, _NTL_LINE_COUNTS[i] + 1):
        line_no = "%s-L%d" % (order_no, j)
        lines.append({"line_no": line_no, "sku": "SKU-%03d" % (i * 10 + j),
                      "quantity": i + j, "unit_price": round(9.9 + j * 2.5, 2),
                      "batches": _ntl_batches(line_no, i, j)})
    return {"order_no": order_no, "customer": "客户%d" % i,
            "amount": round(sum(x["quantity"] * x["unit_price"] for x in lines), 2),
            "placed_at": iso(BASE_TIME + timedelta(days=i)),
            "lines": lines}


NTL_ORDERS = [_ntl_order(i) for i in (1, 2, 3)]
NTL_LINE_TOTAL = sum(len(o["lines"]) for o in NTL_ORDERS)
NTL_BATCH_TOTAL = sum(len(x["batches"]) for o in NTL_ORDERS for x in o["lines"])


def _scenario_nested_two_level():
    def handler(ctx):
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": NTL_ORDERS, "total": len(NTL_ORDERS)}})

    ep = Endpoint(
        "GET", "/orders", handler,
        operation_id="listTwoLevelOrders", summary="查询订单（两层嵌套数组）",
        description="每条订单有对象数组 lines（主键 line_no），每个 lines 元素里再有对象数组 batches"
                    "（主键 batch_no）。共 %d 条订单、%d 条 lines、%d 条 batches，两层主键全局唯一"
                    % (len(NTL_ORDERS), NTL_LINE_TOTAL, NTL_BATCH_TOTAL),
        record_path="data.items", primary_key="order_no",
        dataset=lambda: list(NTL_ORDERS), doc_sample={},
        note="两层嵌套对象数组：lines（%d 条，主键 line_no）→ lines.batches（%d 条，主键 batch_no）"
             % (NTL_LINE_TOTAL, NTL_BATCH_TOTAL),
        extra_catalog={
            "arrayFields": {"lines": "OBJECT_ARRAY", "lines.batches": "OBJECT_ARRAY"},
            "nestedArrays": [
                {"path": "lines", "primaryKey": "line_no", "expectedTotal": NTL_LINE_TOTAL,
                 "countsPerParent": [len(o["lines"]) for o in NTL_ORDERS]},
                {"path": "lines.batches", "primaryKey": "batch_no",
                 "expectedTotal": NTL_BATCH_TOTAL,
                 "countsPerParent": [len(x["batches"]) for o in NTL_ORDERS for x in o["lines"]]},
            ]},
    )
    return Scenario("nested-two-level", "两层嵌套数组",
                    "记录里的对象数组的元素再带一层对象数组，两层都有可作主键的字段",
                    [ep], auth=NO_AUTH)


# ==================== special-field-names：特殊字段名 ====================

# 三个对象数组字段各占一种特殊命名（连字符 / 中文 / 数字开头），
# 每个数组的三个子字段也是三种情形各一个；每个数组的第一个子字段是该数组的主键
SFN_ARRAYS = [
    ("sku-list", ["sku-code", "库存", "1st_price"]),
    ("规格列表", ["spec-name", "取值", "2nd_unit"]),
    ("3d_models", ["model-id", "格式", "3d_size"]),
]


def _sfn_product(i):
    pid = "SFN-%05d" % i
    return {
        "id": pid, "name": "特殊字段商品%d" % i, "price": round(19.9 + i * 3.3, 2),
        "sku-list": [{"sku-code": "%s-SKU%d" % (pid, j), "库存": i * 10 + j,
                      "1st_price": round(5.5 + i + j * 0.25, 2)} for j in (1, 2)],
        "规格列表": [{"spec-name": "%s-规格%d" % (pid, j), "取值": ["红色", "大号"][j - 1],
                    "2nd_unit": ["件", "箱"][j - 1]} for j in (1, 2)],
        "3d_models": [{"model-id": "%s-M%d" % (pid, j), "格式": ["glb", "obj"][j - 1],
                       "3d_size": i * 100 + j} for j in (1, 2)],
    }


SFN_PRODUCTS = [_sfn_product(i) for i in (1, 2, 3)]


def _scenario_special_field_names():
    def handler(ctx):
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": SFN_PRODUCTS, "total": len(SFN_PRODUCTS)}})

    names = "；".join("%s（子字段 %s）" % (name, "、".join(children)) for name, children in SFN_ARRAYS)
    ep = Endpoint(
        "GET", "/products", handler,
        operation_id="listSpecialFieldNameProducts", summary="查询商品（数组字段与子字段名含特殊字符）",
        description="每条记录有三个对象数组字段，字段名分别含连字符、中文、以数字开头，"
                    "各自的子字段也是这三种情形各一个：%s。每个数组 2 条元素" % names,
        record_path="data.items", primary_key="id",
        dataset=lambda: list(SFN_PRODUCTS), doc_sample={},
        note="特殊字段名：%s" % names,
        extra_catalog={
            "arrayFields": {name: "OBJECT_ARRAY" for name, _ in SFN_ARRAYS},
            "nestedArrays": [
                {"path": name, "primaryKey": children[0], "fields": list(children),
                 "expectedTotal": sum(len(p[name]) for p in SFN_PRODUCTS)}
                for name, children in SFN_ARRAYS]},
    )
    return Scenario("special-field-names", "特殊字段名",
                    "数组字段及其子字段的名字含连字符、中文、以数字开头",
                    [ep], auth=NO_AUTH)


def build_scenarios():
    return [
        _scenario_echo(),
        _scenario_single_object(),
        _scenario_biz_code(),
        _scenario_nested(),
        _scenario_required_business_param(),
        _scenario_dotted_param(),
        _scenario_shared_params(),
        _scenario_root_object_record(),
        _scenario_form_body_post(),
        _scenario_nested_two_level(),
        _scenario_special_field_names(),
    ]
