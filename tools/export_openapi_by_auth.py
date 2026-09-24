#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按鉴权类型导出合并的 OpenAPI YAML 与汇总说明，写进仓库的 `openapi/` 目录。

    python3 tools/export_openapi_by_auth.py            # 生成 / 覆盖 openapi/*.yaml 与 openapi/README.md
    python3 tools/export_openapi_by_auth.py --check    # 只比对，不一致返回 1（CI / 自测用）

生成物的 servers 固定为 http://127.0.0.1:18090；换端口启动时请改用服务在线提供的
`/openapi/<鉴权类型>.yaml`，它的地址跟随实际端口。改了场景后重跑本脚本，勿手改生成物。
"""

import argparse
import json
import os
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from dump_scenario_table import auth_cell, pagination_cell  # noqa: E402
from scenarios import REGISTRY  # noqa: E402
from scenarios.bundle import ALL, KIND_TITLES, auth_kinds, build_bundle, scenarios_of  # noqa: E402
from scenarios.yamlout import dump as yaml_dump  # noqa: E402

BASE = "http://127.0.0.1:18090"
OUT_DIR = os.path.join(ROOT, "openapi")
YAML_HEADER = ("# 由 tools/export_openapi_by_auth.py 生成，勿手改。\n"
               "# servers 固定为 %s；换端口时改用服务在线提供的 /openapi/%s.yaml\n")


# ==================== curl 示例 ====================

def _q(text):
    return shlex.quote(text)


def _token_step(flow, scenario_base):
    """取令牌的 curl，结果放进 shell 变量 TOKEN"""
    url = "$B" + flow["url"][len(BASE):]
    pick = "".join("[%s]" % json.dumps(p) for p in flow["tokenPath"].split("."))
    parse = "python3 -c %s" % _q("import sys,json;print(json.load(sys.stdin)%s)" % pick)
    if flow["kind"] == "REQUEST_TOKEN":
        body = json.dumps(flow["requestBody"], ensure_ascii=False)
        cmd = "curl -s -X POST \"%s\" -H 'Content-Type: application/json' -d %s" % (url, _q(body))
    else:
        cmd = "curl -s -X POST \"%s\" -d grant_type=%s" % (url, flow["grantType"])
        if flow["clientAuthMethod"] == "basic":
            cmd += " -u %s:%s" % (flow["clientId"], flow["clientSecret"])
        else:
            cmd += " -d client_id=%s -d client_secret=%s" % (flow["clientId"], flow["clientSecret"])
        if flow.get("scope"):
            cmd += " -d scope=%s" % flow["scope"]
    return "TOKEN=$(%s | %s)" % (cmd, parse)


def curl_example(scenario):
    """调该场景第一个 DATA 端点的 curl；令牌类先取令牌再注入"""
    endpoint = next(e for e in scenario.endpoints if e.role == "DATA")
    scenario_base = "%s/s/%s" % (BASE, scenario.name)
    spec = scenario.auth.describe(scenario_base)
    flow = spec["tokenFlow"]
    lines = []
    if flow:
        lines.append(_token_step(flow, scenario_base))

    headers, query, cookies = [], [], []
    params = [(c["in"], c["name"], c["testValue"], c.get("prefix")) for c in spec["credentials"]]
    params += [(e["in"], e["name"], e["value"], None) for e in spec["extraAuthParams"]]
    for where, name, value, prefix in params:
        if value.startswith("<"):            # 令牌接口签发的值
            value = ("%s $TOKEN" % prefix) if prefix else "$TOKEN"
        {"HEADER": headers, "QUERY": query, "COOKIE": cookies}[where].append((name, value))

    url = "$B/s/%s%s" % (scenario.name, endpoint.path)
    if query:
        url += "?" + "&".join("%s=%s" % kv for kv in query)
    parts = ["curl -s \"%s\"" % url]
    parts += ["-H \"%s: %s\"" % kv for kv in headers]
    if cookies:
        parts.append("-b \"%s\"" % "; ".join("%s=%s" % kv for kv in cookies))
    lines.append(" ".join(parts))
    return "\n".join(lines)


# ==================== 汇总说明 ====================

def _endpoint_counts(scenarios):
    counts = {"DATA": 0, "TOKEN": 0, "FAULT": 0}
    for s in scenarios:
        for e in s.endpoints:
            counts[e.role] = counts.get(e.role, 0) + 1
    return counts


def _kind_title(kind):
    return "全部场景" if kind == ALL else KIND_TITLES.get(kind, (kind, ""))[0]


def render_readme(kinds):
    out = []
    w = out.append
    total_eps = sum(len(s.endpoints) for s in REGISTRY.values())
    w("# 按鉴权类型拆分的 OpenAPI 文档")
    w("")
    w("> 由 `tools/export_openapi_by_auth.py` 生成，**勿手改**；改了场景后重跑该脚本。")
    w("")
    w("本目录把测试 API 提供服务的 %d 个场景 / %d 个端点按**鉴权类型**合并成几份 OpenAPI 3.0.3 YAML，"
      "供其他项目一次读出某类鉴权下的全部接口，再逐个调用做测试。文档由场景注册表生成，"
      "与服务的实际行为一致（单场景文档见 `/s/<场景>/openapi.json`）。" % (len(REGISTRY), total_eps))
    w("")
    w("## 文件一览")
    w("")
    w("| 鉴权类型 | 说明 | 静态文件 | 在线地址 | 场景 | 端点（DATA / TOKEN / FAULT） |")
    w("|---|---|---|---|---|---|")
    for kind in kinds + [ALL]:
        scenarios = scenarios_of(kind)
        c = _endpoint_counts(scenarios)
        desc = "全部鉴权类型" if kind == ALL else KIND_TITLES.get(kind, (kind, ""))[1]
        w("| `%s` %s | %s | [`%s.yaml`](%s.yaml) | `%s/openapi/%s.yaml` | %d | %d / %d / %d |" % (
            kind, _kind_title(kind), desc, kind, kind, BASE, kind, len(scenarios),
            c["DATA"], c["TOKEN"], c["FAULT"]))
    w("")
    w("## 本地启动与获取文档")
    w("")
    w("```bash")
    w("./run.sh                        # 前台启动，监听 %s（Ctrl+C 退出，用完请停掉）" % BASE)
    w("./run.sh --port 9001 --quiet    # 换端口 / 不打印每条请求")
    w("")
    w("curl -s %s/health                                # {\"status\":\"ok\"}" % BASE)
    w("curl -s %s/openapi/bearer.yaml                   # 某类鉴权的合并文档（YAML）" % BASE)
    w("curl -s %s/openapi/bearer.json                   # 同一份文档的 JSON 版" % BASE)
    w("curl -s %s/__admin/scenarios | python3 -m json.tool   # 清单，openapiBundles 列出全部合并文档地址" % BASE)
    w("```")
    w("")
    w("- 服务只依赖 Python 3 标准库，不访问外网。")
    w("- 本目录的静态文件 `servers` 固定为 `%s`；**换端口启动时改用在线地址**，其 `servers` 跟随实际端口。" % BASE)
    w("- 在线文档与静态文件由同一段代码生成，`tests/test_bundle.py` 会校验静态文件没有过期。")
    w("")
    w("## 文档结构与解析约定")
    w("")
    w("| 位置 | 含义 |")
    w("|---|---|")
    w("| `servers[0].url` | 服务根地址；路径统一带场景前缀 `/s/<场景>/...` |")
    w("| `tags[]` | 每个场景一个 tag，`name` = 场景名 |")
    w("| `tags[].x-auth` | 该场景的测试凭证：`credentials`（位置 / 名称 / 测试值 / 前缀）、"
      "`extraAuthParams`（附加鉴权参数）、`tokenFlow`（取令牌方式、令牌路径、注入位置） |")
    w("| `tags[].x-openapi-url` | 该场景的单场景文档地址 |")
    w("| `components.securitySchemes` | 按场景改名为 `<场景>.<原名>`（不同场景的同名 scheme 配置不同） |")
    w("| `paths.*.*.security` | 该接口要用的 scheme；`[]` 表示不需要鉴权（登录 / 令牌接口） |")
    w("| `paths.*.*.operationId` | `<场景驼峰>_<原 operationId>`，全文唯一；原值在 `x-original-operation-id` |")
    w("| `paths.*.*.x-scenario` / `x-auth-type` | 所属场景 / 鉴权类型 |")
    w("| `paths.*.*.x-test.role` | `DATA` 业务数据接口（该调、该同步）/ `TOKEN` 登录与令牌接口（**不要当数据接口**）/ `FAULT` 故障注入 |")
    w("| `paths.*.*.x-test` 其余字段 | `paginationType` + `pagination`（分页参数名与位置）、`recordPath`、`primaryKey`、"
      "`expectedTotal`（期望总条数）、`requiresAuth`、`note`，与 `/__admin/scenarios` 一致 |")
    w("")
    w("## 推荐测试流程")
    w("")
    w("1. 启动服务，读取某份合并文档，按 `x-test.role == DATA` 过滤出要测的接口。")
    w("2. `POST %s/__admin/reset?scenario=<场景>` 复位该场景（清请求日志、作废令牌）。" % BASE)
    w("3. 按 tag 的 `x-auth` 配置凭证；有 `tokenFlow` 的先调令牌接口取令牌，再按 `inject` 注入。")
    w("4. 按 `x-test.pagination` 翻页取完全部记录，从 `recordPath` 取记录，断言条数等于 `expectedTotal`。")
    w("5. `GET %s/__admin/requests?scenario=<场景>` 查看服务端实际收到的请求，断言鉴权位置、翻页参数等。" % BASE)
    w("")
    w("注意：`/__admin/config` 与 `faults` 场景的计数器是进程级的，改配置或跑 `faults` 的用例需独占实例或串行；"
      "`oauth2-paged-expiring` 每页服务端延迟 6 秒，一次完整同步约 30 秒。各端点的特殊语义见仓库根目录 README.md。")
    w("")
    w("## 各鉴权类型明细")
    for kind in kinds:
        scenarios = scenarios_of(kind)
        title, desc = KIND_TITLES.get(kind, (kind, ""))
        w("")
        w("### `%s` %s — [`%s.yaml`](%s.yaml)" % (kind, title, kind, kind))
        w("")
        w("%s。" % desc)
        w("")
        w("| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |")
        w("|---|---|---|---|---|---|---|---|")
        for scenario in scenarios:
            base = "%s/s/%s" % (BASE, scenario.name)
            for index, endpoint in enumerate(scenario.endpoints):
                catalog = endpoint.catalog(base)
                first = index == 0
                total = catalog["expectedTotal"]
                w("| %s | %s | `%s /s/%s%s`<br>%s | %s | %s | %s | %s | %s |" % (
                    ("**%s**" % scenario.name) if first else "",
                    scenario.covers if first else "",
                    endpoint.method, scenario.name, endpoint.path, catalog["role"],
                    auth_cell(scenario, base) if first else "",
                    pagination_cell(catalog),
                    "根数组（空串）" if catalog["recordPath"] == "" else (
                        "`%s`" % catalog["recordPath"] if catalog["recordPath"] else "—"),
                    "`%s`" % catalog["primaryKey"] if catalog["primaryKey"] else "无",
                    "—" if total is None else total))
        if kind == "none":
            continue
        w("")
        w("快速验证（`B=%s`）：" % BASE)
        w("")
        w("```bash")
        w("B=%s" % BASE)
        for scenario in scenarios:
            w("# %s" % scenario.name)
            w(curl_example(scenario))
        w("```")
    w("")
    return "\n".join(out)


# ==================== 生成与落盘 ====================

def render_all():
    """{相对 openapi/ 的文件名: 文本}"""
    kinds = auth_kinds()
    files = {}
    for kind in kinds + [ALL]:
        files["%s.yaml" % kind] = (YAML_HEADER % (BASE, kind)) + yaml_dump(build_bundle(kind, BASE))
    files["README.md"] = render_readme(kinds)
    return files


def main():
    parser = argparse.ArgumentParser(description="按鉴权类型导出合并的 OpenAPI YAML")
    parser.add_argument("--check", action="store_true", help="只比对生成物是否过期，不写文件")
    args = parser.parse_args()

    files = render_all()
    stale = []
    for name, text in files.items():
        path = os.path.join(OUT_DIR, name)
        current = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                current = fh.read()
        if current != text:
            stale.append(name)
            if not args.check:
                os.makedirs(OUT_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
    if args.check:
        if stale:
            print("openapi/ 下这些文件已过期，请重跑 python3 tools/export_openapi_by_auth.py：%s"
                  % ", ".join(stale))
            return 1
        print("openapi/ 生成物是最新的")
        return 0
    print("已写入 %s：%s" % (OUT_DIR, ", ".join(stale) if stale else "（无变化）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
