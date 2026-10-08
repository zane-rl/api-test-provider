#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从场景注册表导出 README 里的「场景总表」。

README 的表格不手写：改了场景后跑

    python3 tools/dump_scenario_table.py > /tmp/table.md

再把结果替换进 README.md 的 `<!-- SCENARIO-TABLE -->` 区块。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib.parse import urlencode  # noqa: E402

from scenarios import REGISTRY  # noqa: E402
from scenarios.common import FORM_MEDIA_TYPE  # noqa: E402

BASE = "http://127.0.0.1:18090"


def auth_cell(scenario, base):
    spec = scenario.auth.describe(base)
    if not spec["credentials"] and not spec["extraAuthParams"]:
        return "无"
    parts = ["%s `%s` = `%s`" % (c["in"], c["name"], c["testValue"])
             for c in spec["credentials"]]
    parts += ["附加 %s `%s` = `%s`" % (e["in"], e["name"], e["value"])
              for e in spec["extraAuthParams"]]
    flow = spec["tokenFlow"]
    if flow and flow["kind"] == "REQUEST_TOKEN":
        if flow.get("contentType") == FORM_MEDIA_TYPE:
            body = "表单 `%s`" % urlencode(flow["requestBody"])
        else:
            body = "体 `%s`" % json.dumps(flow["requestBody"], ensure_ascii=False)
        parts.append("令牌：`POST %s`，%s，取 `%s`"
                     % (flow["url"].replace(base, ""), body, flow["tokenPath"]))
    elif flow:
        parts.append("令牌：`POST %s`，client_secret_%s，`%s` / `%s`"
                     % (flow["url"].replace(base, ""), flow["clientAuthMethod"],
                        flow["clientId"], flow["clientSecret"]))
    return "<br>".join(parts)


def pagination_cell(catalog):
    kind = catalog["paginationType"]
    if kind == "NONE":
        return "NONE"
    detail = []
    pg = catalog["pagination"]
    for key in ("pageParam", "offsetParam", "cursorParam", "pageSizeParam", "limitParam"):
        if pg.get(key):
            detail.append("%s(%s)" % (pg[key]["name"], pg[key]["in"]))
    if pg.get("nextUrlPath"):
        detail.append("%s / %s" % (pg["nextUrlPath"], pg.get("nextUrlStyle", "")))
    if pg.get("startFrom") is not None:
        detail.append("起始 %s" % pg["startFrom"])
    if pg.get("fixedPageSize"):
        detail.append("固定每页 %d" % pg["fixedPageSize"])
    return "%s<br>%s" % (kind, " ".join("`%s`" % d for d in detail))


def record_path_cell(catalog):
    """记录路径：空串表示响应的根（根数组，或根对象即一条记录）"""
    if catalog["recordPath"] == "":
        return "根（空串）"
    return "`%s`" % catalog["recordPath"] if catalog["recordPath"] else "—"


def main():
    group = None
    for name, scenario in REGISTRY.items():
        base = "%s/s/%s" % (BASE, name)
        if getattr(scenario, "group", "") != group:
            group = scenario.group
            print("\n#### %s\n" % group)
            print("| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |")
            print("|---|---|---|---|---|---|---|---|")
        for index, endpoint in enumerate(scenario.endpoints):
            catalog = endpoint.catalog(base)
            total = catalog["expectedTotal"]
            first = index == 0
            print("| %s | %s | `%s %s`<br>%s | %s | %s | %s | %s | %s |" % (
                ("**%s**" % name) if first else "",
                scenario.covers if first else "",
                endpoint.method, endpoint.path, catalog["role"],
                auth_cell(scenario, base) if first else "",
                pagination_cell(catalog),
                record_path_cell(catalog),
                "`%s`" % catalog["primaryKey"] if catalog["primaryKey"] else "无",
                "—" if total is None else total))


if __name__ == "__main__":
    main()
