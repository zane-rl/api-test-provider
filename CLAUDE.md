# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

HDP（明道云数据集成中心）「API 数据源」全链路自动化测试的**被测对端**：纯 Python 3 标准库的本机 HTTP 服务，
不装任何第三方依赖、不访问外网。每个场景自带一份与实现一致的 OpenAPI 3.0.3 文档（`.json` / `.yaml` / `.yml` 三种扩展名），外部 Java 集成测试喂文档给后端、
让后端来请求本服务，再用 `/__admin/scenarios`（期望值）与 `/__admin/requests`（实际收到的请求）断言。
README.md 是完整的使用说明与场景语义表，改行为前先读它对应的段落。

## 常用命令

```bash
./run.sh                          # 前台启动，监听 127.0.0.1:18090（Ctrl+C 退出）
./run.sh --port 9001 --quiet      # 换端口 / 不打印每条请求
./stop.sh                         # 停掉 18090 上的实例；--port 9001 停指定端口（只停本仓库的 server.py，别的进程不碰）
./run_tests.sh                    # 全部自测（unittest discover，-v 看用例名）
python3 -m unittest tests.test_auth                               # 单个文件
python3 -m unittest tests.test_auth.<类名>.<方法名>               # 单个用例
python3 tools/dump_scenario_table.py   # 重新生成 README 场景总表
python3 tools/export_openapi_by_auth.py           # 重新生成 openapi/（按鉴权类型的合并文档 .yaml / .yml / .json + 汇总说明）
python3 tools/export_openapi_by_auth.py --check   # 只检查 openapi/ 是否过期
```

当前规模：56 个场景 / 112 个端点 / 233 条自测（A 鉴权 26、B 分页 9、C 请求与响应 11、D 依赖 5、E 故障 4、F 类型不一致 1）。

测试在随机端口自起服务（`tests/support.py` 的 `ProviderTestCase`，每个测试类一个实例，`setUp` 会 `POST /__admin/reset`），
不会和已启动的 18090 冲突。没有 lint / 构建步骤。
`ProviderTestCase.setUpClass` 会把 `tokenSlowDelaySeconds` 调成 0（否则遍历全部场景的用例每取一次 `token-slow` 的令牌就等 5 秒），
`tearDownClass` 调回缺省值；要验延迟的用例自己临时调。

18090 上可能有联调 / E2E 共用的常驻实例：**停它或重启它之前先得到用户同意**；自己验证用别的端口临时起
（`./run.sh --port 18091 --quiet`），用完 `./stop.sh --port 18091` 停掉。
共用实例上复位要带场景名（`POST /__admin/reset?scenario=<场景>`）：不带场景名会清掉所有人的请求日志与令牌，
并还原全部故障开关。新场景只有重启实例后才生效（注册表在进程启动时建好，没有热加载）。

## 架构要点

- `server.py`：`BaseHTTPRequestHandler`、路由、内存请求日志（最多 5000 条）、`/health` 与 `/__admin/*`
  （含按场景名分发的 `/__admin/faults?scenario=<场景>`）。
  业务路径统一为 `/s/<场景>/<端点>`，按场景名查 `REGISTRY` 再按 `Endpoint.match` 匹配路径模板。
  文档地址 `/s/<场景>/openapi.<json|yaml|yml>` 与 `/openapi/<鉴权类型|all>.<json|yaml|yml>` 都经 `doc_response`：
  同一个文档对象按扩展名选 JSON / YAML 序列化，**不要为某种扩展名单独拼内容**。
  自己实现了 `Transfer-Encoding: chunked` 请求体读取（JDK HttpClient 默认走分块）。
- `scenarios/common.py`：核心抽象。`Ctx`（请求上下文）/ `Resp` / `ApiError`、`AuthRequirement` + `AuthSpec`
  （同一个对象既生成 securityScheme、又做运行时校验、又产出清单里的测试凭证）、`Endpoint` / `Scenario`
  （公共参数用 `Scenario(shared_parameters=...)` + `ref_param()`，路径项级参数用 `Endpoint(path_parameters=...)`；
  参数声明 `qp` / `hp` / `cp` / `pp` 分别是 Query / Header / Cookie / Path；场景专有的清单字段放 `Endpoint(extra_catalog=...)`）、
  `require_media_type`（只认一种 Content-Type 的端点，不符返回 415）、
  确定性造数（`build_record` / `build_dataset`，固定基准时间 `BASE_TIME`）、令牌仓库、schema 推断。
- `scenarios/{auth,pagination,payload,depend,faults,typemismatch}.py`：A~F 六类场景，每个模块暴露 `build_scenarios()`；
  `scenarios/__init__.py` 按 `GROUPS` 顺序汇总成有序的 `REGISTRY`（顺序即清单 / README 展示顺序，重名启动即报错），
  `reset_all` 负责按场景复位（令牌、`faults` 计数器、各场景的故障开关）。
- E 组里带故障开关的三个场景由 `faults.build_scenarios()` 一并注册：`scenarios/switchable.py`（`depend-switchable`，
  模块级开关，先有的，保持原样）、`scenarios/faultcases.py`（`depend-two-roots`、`page-fail-midway`）。
  `scenarios/faultswitch.py` 是通用开关板 `SwitchBoard` 与登记表 `BOARDS`（`depend-switchable` 经适配器登记进去）；
  `/__admin/faults`、`reset_all`、`/__admin/state` 的 `faultSwitchesByScenario` 都只认 `BOARDS`。
- `scenarios/auth.py` 里的 `CONFIG` / `CONFIG_BOUNDS` 是 `/__admin/config` 的全部配置项（含 `token-slow` 的 `tokenSlowDelaySeconds`）。
  `_login_endpoint` / `_bearer_request_scenario` / `_oauth_token_endpoint` 带可选参数（附加返回字段、延迟、`required_scope`），
  缺省值就是最基本的形态，多个场景共用——改它们会同时影响所有取令牌场景的文档。
- `scenarios/negative.py`：反例文档（Swagger 2.0、OpenAPI 3.1.0），路径在 `/__negative/` 下，期望后端**拒绝导入**。
  它不进 `REGISTRY`、不计入场景数、不进合并文档；清单在 `/__admin/scenarios` 的 `negativeDocuments`。
- `scenarios/openapi.py`：遍历注册表**动态生成** OpenAPI 3.0.3。正常场景**不提供** Swagger 2.0 / OpenAPI 3.1
  （产品口径不支持）；文件里的 Swagger 2.0 转换器只给 `negative.py` 用。
  200 响应 schema 靠「用 `doc_sample` 真调一次处理函数」推断，此时 `ctx.doc_mode = True`。
  文档按 `(版本, 场景, base_url)` 进程内缓存，**生成文档期的处理函数输出必须与配置无关且确定**。
- `scenarios/bundle.py`：按 `AuthSpec.kind` 把单场景文档**改名合并**成一份（路径加 `/s/<场景>` 前缀、
  securitySchemes 改名 `<场景>.<原名>` 并下放到 operation、公共参数 `components.parameters` 同样改名且引用处保留 `$ref`、
  operationId 加场景前缀、`x-auth` / `x-test` 扩展），
  在线由 `/openapi/<鉴权类型|all>.yaml|.yml|.json` 提供，静态版由 `tools/export_openapi_by_auth.py` 写进 `openapi/`
  （每种类型 `.yaml` / `.yml` / `.json` 三个文件，生成物，勿手改）。
  YAML 由 `scenarios/yamlout.py` 输出（标准库实现，按 YAML 1.1 保守加引号，因为 Java 侧 SnakeYAML 是 1.1 语义）。

## 改代码时必须守住的约束

- **文档 / 实现 / 清单不能漂移**：一个端点的处理函数、参数、请求体 schema、分页元数据、`record_path`、`primary_key`、
  `dataset` 都写在同一个 `Endpoint(...)` 构造里；`expectedTotal` / `sampleFirst3` 由 `dataset` 实时算，不要写死常量。
- **数据必须确定**：不要用 `now()`、随机数；所有记录由公式 + `BASE_TIME` 生成。
- **有副作用的端点**（计数、sleep、签发令牌）必须显式给 `response_schema`——注意 `Endpoint.__init__` 会把
  `doc_sample=None` 变成 `{}`，所以不给 `response_schema` 时处理函数仍会在生成文档时被调用。
  需要 sleep 的处理函数要判断 `ctx.doc_mode`，否则取文档会被拖住（见 `faults.py` `/slow`、`auth.py` 分页延迟与登录延迟）。
  带开关的场景走另一条路：不显式给 schema，而是在 `ctx.doc_mode` 下跳过开关与计数（`SwitchBoard.guarded`）。
- **改共用的构造函数后要证明现有场景没变**：把上一个提交 `git archive` 到临时目录，对每个场景比对
  `build_openapi(...)`、`auth.describe(...)`、`endpoint.catalog(...)` 的输出。H-VERIFY-1013 时这个比对抓到过一次
  把 5 个现有 OAuth2 场景文档改坏的错误，自测当时并没有报。
- **另外几处故意「不寻常」的地方，不要当 bug 修**：`page-fail-midway` 的开关缺省就是 `fail`（第 3 页起 500），复位回到失败；
  `depend-empty-parent` 的 `/categories` 恒定 0 条，但文档示例带一条示意记录（空数组推断不出字段，所以 schema 显式声明）；
  `depend-mixed-key` 的 `/shops` 里 `ref` 依次为 `1`、`"1"`、`2`、`"3"`，schema 显式写成 `oneOf` 整数 / 文本；
  `oauth2-scope-required` 是唯一校验 scope 的 OAuth2 场景，其余 6 个只回显。
- 依赖类场景清单里的「两两组合合计」（`referenceTotalIfAllCombinations` / `referenceTotals`）只是参考值，
  不要写成「后端应该这样组合」；`dependsOn` 在一个下游依赖两个前置时是数组，其余是对象。
- **`type-mismatch` 场景的 schema 是故意手写错的**（全项目唯一例外），不要当 bug 修；`tests/test_docs.py`
  的漂移用例对它豁免类型比对。期望值在清单的 `declaredVsActual` 里。
- `oauth2-paged-expiring` 的 ttl 35 秒 / 每页延迟 6 秒是按后端「≤60 秒短效令牌按一半有效期复用」的规则算出来的，
  不要随意调；`oauth2PagedTtlSeconds` 不能调过 60。原因见 README 对应段落。
- `/__admin/config`、故障计数器、各场景的故障开关都是**进程级全局**状态；`/__admin/reset` 不会还原 config，测试里用 `set_config` 改了要自己恢复。
  自测都在同一个进程里起服务，所以一个测试类改了 config 不恢复会影响后面的测试类。
- admin 接口不进 OpenAPI 文档、也不进请求日志。
- **路径项（path item）里不只有 HTTP 方法**：`shared-params` 的 `/customers` 有路径项级 `parameters`，参数列表里也可能是
  `{"$ref": ...}` 而没有 `name`。遍历文档的代码用 `openapi.HTTP_METHODS`（测试里用 `support.operations_of`）过滤，别直接 `for method in path_item`。

## 新增场景的流程

按 README「如何新增一个场景」操作，要点：写 `_scenario_xxx()` 并加进模块的 `build_scenarios()`
（要带故障开关就用 `faultswitch.SwitchBoard` 并 `faultswitch.register`，参照 `faultcases.py`）；
同步**数量断言**：`tests/test_faults_admin.py`（场景总数）、`tests/test_docs.py`（端点总数），
新增 A 类鉴权场景或 B 类数据端点时还有 `tests/test_auth.py` / `tests/test_pagination.py`；
跑 `./run_tests.sh`；跑 `tools/export_openapi_by_auth.py` 重新生成 `openapi/`（否则 `tests/test_bundle.py` 报过期；
新的鉴权类型要在 `bundle.py` 的 `KIND_TITLES` 补中文名）；再用 `tools/dump_scenario_table.py` 重新生成表格，替换 README 中
`<!-- SCENARIO-TABLE:BEGIN -->` ~ `<!-- SCENARIO-TABLE:END -->` 区块（勿手改）；并在 README「变更记录」追加一行
（场景数 / 端点数 / 自测条数），同步 README 的目录结构、合并文档表里的场景数，以及本文件「常用命令」下的当前规模。

## 约定

- 代码注释、文档、提交信息使用中文；提交信息格式 `类型(模块):主题`（冒号后不带空格），如 `feat(scenarios):...`、`fix(faults):...`。
- 代码中的凭证都是明显的测试假值（`test-key-123` 等），新增场景沿用这种写法。
