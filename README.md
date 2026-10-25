# 测试用 API 提供服务（api-test-provider）

给 HDP（明道云数据集成中心）「API 数据源」做**全链路自动化验证**的被测对端：
一个只用 Python 标准库的本机 HTTP 服务，覆盖后端支持的全部鉴权 / 分页 / 请求体 / 响应 /
依赖 / 故障形态，**每个场景自带一份与实现完全一致的 OpenAPI 文档**。

典型用法：Java 集成测试把某个场景的 `openapi.json` 喂给后端 → 后端解析出接口、配好鉴权 →
后端真的来请求本服务 → 断言中间库里的数据条数与 `/__admin/scenarios` 声明的 `expectedTotal` 一致 →
再用 `/__admin/requests` 断言后端实际发出的请求（鉴权放对位置了吗、翻页参数对吗、`+` 编码了吗）。

两个硬保证：

1. **数据确定可预期**——所有记录由固定公式 + 固定基准时间（`2026-01-01T08:00:00+08:00`）生成，
   不用 `now()`、不用随机数，重启后完全一致。
2. **收到的请求可查询**——每个业务 / 令牌 / 登录请求都进内存日志，`/__admin/requests`
   可查解码后的 Query、全部请求头、Cookie、原始请求体与响应码。

> 代码里出现的所有凭证都是明显的测试假值（`test-key-123` 之类），不含任何真实密钥。

**10月26日研发演示：** [25分钟完整主线＋10分钟备用](docs/DEMO-20261026.md)；[Linux/Docker部署与IP地址配置](docs/DEPLOYMENT.md)。离线包提供全部56个场景的可上传JSON、分幕索引和文件哈希，主线覆盖正式保存、首次同步、查看数据和保存为空间API。生成器不操作HDP资源；正式步骤须取得当次授权。目标研发环境页面排练另记。

## 启动

```bash
./run.sh                 # 监听 http://127.0.0.1:18090
./run.sh --port 9001     # 换端口（文档里的 servers[0].url 会跟着变）
./run.sh --quiet         # 不打印每条请求
./run.sh --host 0.0.0.0 --port 18090 --advertise-base-url http://10.0.0.8:18090  # 换成研发IP

./stop.sh                # 停掉 127.0.0.1:18090 上的实例
./stop.sh --port 9001    # 停掉指定端口上的实例
```

不依赖外网、不装任何第三方库，Python 3 即可。`run.sh` 是前台运行（Ctrl+C 退出）；需要后台常驻时用
`nohup ./run.sh --quiet > provider.log 2>&1 &`，不再需要时用 `./stop.sh` 停掉，不要留下没人用的实例占端口。

`stop.sh` 只停「监听该端口、命令行是 `server.py`、工作目录是本仓库」的进程：端口上没有进程时什么都不做（退出码 0），
被别的程序占用时拒绝并返回 1，不会误杀。**联调 / E2E 共用的常驻实例不要随手停**，先确认没人在用。

研发部署必须显式指定`--advertise-base-url`，否则保持原本机地址。它只接受HTTP(S)源地址，不接收凭证、路径或查询参数；文档、令牌地址和同源绝对分页链接都使用它，不信任请求Host头。监听地址与外部IP/端口可不同，适用于Docker端口映射。

打包和材料生成（只写指定本地目录，不访问HDP或共享服务）：

```bash
python3 tools/build_demo_bundle.py --output dist --base-url http://10.0.0.8:18090
python3 tools/prepare_demo.py --base-url http://10.0.0.8:18090 --output dist/demo-materials-1026
python3 tools/check_demo.py --base-url http://10.0.0.8:18090
```

没有真实研发IP时可省略打包的`--base-url`，包中会明确标记为地址模板；部署前重新生成真实IP素材。打包白名单排除Git、IDE、日志和本地运行文件，附SHA256校验；已有同名包/素材不覆盖。

快速自检：

```bash
curl -s http://127.0.0.1:18090/health
curl -s http://127.0.0.1:18090/__admin/scenarios | python3 -m json.tool | head -40
curl -s http://127.0.0.1:18090/s/page-number/openapi.json | python3 -m json.tool | head -30
```

## 跑自测

```bash
./run_tests.sh              # 全部用例
./run_tests.sh -v           # 带用例名
python3 -m unittest tests.test_auth   # 只跑一个文件
```

测试自己在随机端口起服务，不会和已启动的实例抢 18090。

## 目录结构

```
server.py                   HTTP 处理、路由、请求日志、/health、/__admin/*、启动参数
scenarios/
  common.py                 Ctx/Resp/ApiError、鉴权模型、Endpoint/Scenario、造数、令牌仓库、schema 推断
  openapi.py                由注册表动态生成 OpenAPI 3.0 文档（Swagger 2.0 转换器只给反例文档用）
  auth.py                   A 鉴权（26 个场景）
  pagination.py             B 分页（9 个场景）
  payload.py                C 请求与响应（11 个场景）
  depend.py                 D 依赖（5 个场景）
  faults.py                 E 故障（4 个场景：faults，以及由它一并注册的 depend-switchable、depend-two-roots、page-fail-midway）
  switchable.py             depend-switchable：分支依赖 + 按接口切换持续失败的开关与调用计数
  faultcases.py             depend-two-roots（一个下游依赖两个根）、page-fail-midway（分页第 3 页起失败）
  faultswitch.py            通用的按接口故障开关与调用计数（SwitchBoard），/__admin/faults 按场景名分发到这里
  typemismatch.py           F 类型不一致（1 个场景，**唯一手写 schema 的地方**）
  negative.py               反例文档：Swagger 2.0 / OpenAPI 3.1.0（应被后端拒绝，不计入场景数）
  bundle.py                 按鉴权类型合并的 OpenAPI（由单场景文档改名合并而来）
  yamlout.py                标准库实现的 YAML 输出器（YAML 1.1 解析器也不会误读）
  __init__.py               REGISTRY：场景注册表（有序），名字重复会直接报错
tools/dump_scenario_table.py  导出下面那张场景总表
tools/export_openapi_by_auth.py  生成 openapi/ 目录（按鉴权类型的合并文档 .yaml / .yml / .json + 汇总说明）
tools/build_demo_bundle.py   生成独立Linux演示包、素材和SHA256清单
tools/prepare_demo.py        按实际研发IP离线生成上传文档、分幕索引与夹具事实
tools/check_demo.py          只读检查部署健康、场景和对外文档地址
tools/demo_scenarios.py      25分钟主线与10分钟备用编排
docs/                       研发部署步骤与演示剧本
Dockerfile                  可选镜像构建入口（Provider运行不需要Docker）
openapi/                    生成物，勿手改：<鉴权类型>.{yaml,yml,json}、all.{yaml,yml,json}、README.md
tests/                      标准库 unittest
```

### 「文档 / 实现 / 清单」三者为什么不会漂移

- **一个端点 = 一个 `Endpoint` 对象**：处理函数、参数声明、请求体 schema、清单元数据
  （分页方式、参数名与位置、`recordPath`、`primaryKey`、数据集）写在同一个构造调用里。
- **OpenAPI 文档是遍历注册表生成的**，没有手写副本；`servers[0].url` 按实际端口生成。
- **200 响应的 schema 由「真的调用一次处理函数」推断**（端点用 `doc_sample` 声明文档期的
  query / body / path），再合并前 5 条样本推出 `nullable` 与 `format`。有副作用的端点
  （故障注入、令牌签发）显式声明 `response_schema`，并由 `tests/test_docs.py` 的漂移用例守着。
- **`components.securitySchemes` + 顶层 `security` 与运行时校验共用同一个 `AuthSpec`**：
  一个 `AuthRequirement` 既生成 securityScheme、又执行校验、又产出清单里的测试凭证。
- **`expectedTotal` / `sampleFirst3` 是实时从数据集算出来的**，不是手写常量；
  依赖类子接口的 `expectedTotal` = 对全部父记录求和。
- **唯一的例外是 `type-mismatch` 场景**：它的 schema 是手写的、**故意**与实际返回的字段类型
  对不上（见下），漂移用例对它豁免类型比对，但可达性照常校验。
- 自测兜底：遍历每份文档声明的每个 path+method 真的发一次请求，断言非 404/405；
  分页用例只读 `/__admin/scenarios` 的分页元数据来翻页，清单写错用例就挂。

## 按鉴权类型拆分的 OpenAPI（给其他项目解析）

需要「一次拿到某类鉴权下全部接口」的消费方，用按鉴权类型合并的文档，而不是逐个场景拉：

| 鉴权类型 | 场景数 | 静态文件 | 在线地址 |
|---|---|---|---|
| `none` 无鉴权 | 29 | `openapi/none.yaml` | `/openapi/none.yaml` |
| `bearer` | 10 | `openapi/bearer.yaml` | `/openapi/bearer.yaml` |
| `apiKey` | 7 | `openapi/apiKey.yaml` | `/openapi/apiKey.yaml` |
| `basic` | 2 | `openapi/basic.yaml` | `/openapi/basic.yaml` |
| `oauth2` | 7 | `openapi/oauth2.yaml` | `/openapi/oauth2.yaml` |
| `fixedParam` | 1 | `openapi/fixedParam.yaml` | `/openapi/fixedParam.yaml` |
| 全部 | 56 | `openapi/all.yaml` | `/openapi/all.yaml` |

- 每份文档都有 `.yaml` / `.yml` / `.json` 三种扩展名（静态文件与在线地址都是，表里只列了 `.yaml`），内容等价；
  在线文档的 `servers` 跟随实际端口，静态文件的 `servers` 固定为 `http://127.0.0.1:18090`。
- 合并规则：路径加 `/s/<场景>` 前缀、securitySchemes 改名为 `<场景>.<原名>` 并下放到每个 operation、
  公共参数（components.parameters）改名为 `<场景>.<原名>` 且引用处保留 `$ref`、
  operationId 加场景前缀；tag 的 `x-auth` 给测试凭证，operation 的 `x-test` 给角色 / 分页 / `expectedTotal`。
- 汇总说明（按鉴权类型的场景明细、每个场景可直接执行的 curl、解析约定、测试流程）见 [`openapi/README.md`](openapi/README.md)。
- 改了场景后跑 `python3 tools/export_openapi_by_auth.py` 重新生成；`tests/test_bundle.py` 会在静态文件过期时报错。

## 场景总表

所有业务路径形如 `/s/<场景>/<端点>`；每个场景的文档在 `/s/<场景>/openapi.json`，另有内容等价的
`/s/<场景>/openapi.yaml` 与 `/s/<场景>/openapi.yml`（**文档端点本身不需要鉴权**）。全部文档都是 OpenAPI 3.0.3，
见下文「文档格式与版本」。

`role` 列：`DATA` = 业务数据接口（测试应勾选并同步）、`TOKEN` = 登录 / 令牌端点（不要勾选）、`FAULT` = 故障注入。

<!-- SCENARIO-TABLE:BEGIN 由 tools/dump_scenario_table.py 生成，勿手改 -->

#### A 鉴权

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **none** | 鉴权方式 NONE | `GET /items`<br>DATA | 无 | NONE | `data.items` | `id` | 20 |
| **bearer-static** | BEARER_TOKEN + tokenSource=STATIC | `GET /items`<br>DATA | HEADER `Authorization` = `Bearer test-token-abc` | NONE | `data.items` | `id` | 20 |
| **bearer-request** | BEARER_TOKEN + tokenSource=REQUEST，令牌注入 Header Authorization | `POST /login`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /login`，体 `{"username": "test", "password": "test-pass"}`，取 `data.accessToken` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **bearer-request-header** | tokenSource=REQUEST，令牌注入自定义 Header、不带 Bearer 前缀 | `POST /login`<br>TOKEN | HEADER `X-Access-Token` = `<由令牌接口签发>`<br>令牌：`POST /login`，体 `{"username": "test", "password": "test-pass"}`，取 `data.accessToken` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **bearer-request-query** | tokenSource=REQUEST，令牌注入 Query | `POST /login`<br>TOKEN | QUERY `access_token` = `<由令牌接口签发>`<br>令牌：`POST /login`，体 `{"username": "test", "password": "test-pass"}`，取 `data.accessToken` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **apikey-header** | API_KEY in=header | `GET /items`<br>DATA | HEADER `X-Api-Key` = `test-key-123` | NONE | `data.items` | `id` | 20 |
| **apikey-query** | API_KEY in=query | `GET /items`<br>DATA | QUERY `api_key` = `test-key-123` | NONE | `data.items` | `id` | 20 |
| **apikey-cookie** | API_KEY in=cookie | `GET /items`<br>DATA | COOKIE `api_key` = `test-key-123` | NONE | `data.items` | `id` | 20 |
| **apikey-header-prefix** | API_KEY 放 Authorization 头且带自定义前缀 Token | `GET /items`<br>DATA | HEADER `Authorization` = `Token test-key-123` | NONE | `data.items` | `id` | 20 |
| **apikey-extra** | API_KEY 主凭证与附加鉴权参数并存 | `GET /items`<br>DATA | HEADER `X-API-Key` = `test-key-123`<br>附加 HEADER `X-App-Key` = `extra-key-1` | NONE | `data.items` | `id` | 20 |
| **basic** | BASIC_AUTH | `GET /items`<br>DATA | HEADER `Authorization` = `Basic dGVzdDp0ZXN0LXBhc3M=` | NONE | `data.items` | `id` | 20 |
| **basic-extra** | BASIC_AUTH 与附加鉴权参数并存 | `GET /items`<br>DATA | HEADER `Authorization` = `Basic dGVzdDp0ZXN0LXBhc3M=`<br>附加 QUERY `tenant` = `t-001` | NONE | `data.items` | `id` | 20 |
| **oauth2-post** | OAUTH2 客户端凭证，凭证放表单体 | `POST /oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **oauth2-basic** | OAUTH2 client_secret_basic + 令牌缓存失效后 401 重取并重放 | `POST /oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_basic，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **oauth2-extra** | OAUTH2 与附加鉴权参数并存；令牌请求不带附加参数 | `POST /oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>附加 HEADER `X-App-Key` = `extra-key-1`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **oauth2-expiring** | OAUTH2 令牌缓存与过期刷新：距过期不足 30 秒应提前重取 | `POST /oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **oauth2-paged-maxuses** | 一个批次内多次业务请求：复用令牌遇 401 → 重取 → 重放同页 | `POST /oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `id` | 50 |
| **oauth2-paged-expiring** | 一个批次内令牌临近过期：后端应主动重取，不应出现 401 | `POST /oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `id` | 50 |
| **fixed-param** | FIXED_PARAM：Header + Query + Cookie 同时必填 | `GET /items`<br>DATA | HEADER `X-App-Id` = `app-001`<br>QUERY `sign` = `test-sign`<br>COOKIE `tenant` = `t-001` | NONE | `data.items` | `id` | 20 |
| **bearer-extra** | BEARER_TOKEN 与附加鉴权参数（Header + Query）并存 | `GET /items`<br>DATA | HEADER `Authorization` = `Bearer test-token-abc`<br>附加 HEADER `X-App-Key` = `extra-key-1`<br>附加 QUERY `sign` = `test-sign` | NONE | `data.items` | `id` | 20 |
| **forbidden** | 401 与 403 分类 | `GET /secret-items`<br>DATA | HEADER `Authorization` = `Bearer test-token-abc` | NONE | `data.items` | `id` | 0 |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **token-json-body** | 取 Token 接口只收 application/json；Token 路径 data.token.value 且带有效期字段 | `POST /auth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /auth/token`，体 `{"appKey": "test-client", "appSecret": "test-secret"}`，取 `data.token.value` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **token-form-body** | 取 Token 接口只收 application/x-www-form-urlencoded；Token 在顶层 tokenValue | `POST /auth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /auth/token`，表单 `appKey=test-client&appSecret=test-secret`，取 `tokenValue` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **token-typed-fields** | 取 Token 返回除令牌（文本）与有效期（整数）外还有布尔、小数字段；有效期路径的候选不应包含布尔字段 | `POST /login`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /login`，体 `{"username": "test", "password": "test-pass"}`，取 `data.accessToken` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **oauth2-scope-required** | OAUTH2 授权范围：令牌请求必须带 scope=read，否则 400 invalid_scope | `POST /oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **token-slow** | 取 Token 接口校验通过后固定延迟若干秒再返回，验证取令牌期间的等待与超时提示 | `POST /login`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /login`，体 `{"username": "test", "password": "test-pass"}`，取 `data.accessToken` | NONE | — | 无 | — |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |

#### B 分页

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **root-array** | 记录路径 = 根数组；分页方式 NONE | `GET /warehouses`<br>DATA | 无 | NONE | 根（空串） | `id` | 4 |
| **page-number** | PAGE_NUMBER × QUERY；startFrom 1/0；不配每页数量靠空页结束 | `GET /products`<br>DATA | 无 | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `sku` | 57 |
|  |  | `GET /products0`<br>DATA |  | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 0` | `data.items` | `sku` | 57 |
|  |  | `GET /products-nosize`<br>DATA |  | PAGE_NUMBER<br>`page(QUERY)` `起始 1` `固定每页 10` | `data.items` | `sku` | 57 |
| **offset** | OFFSET × QUERY；totalPath 结束；记录无主键 | `GET /movements`<br>DATA | 无 | OFFSET<br>`offset(QUERY)` `limit(QUERY)` `起始 0` | `data.items` | 无 | 103 |
| **cursor-query** | CURSOR × QUERY；next_cursor 为 null 结束 | `GET /events`<br>DATA | 无 | CURSOR<br>`cursor(QUERY)` `limit(QUERY)` | `data` | `event_id` | 73 |
| **cursor-header** | CURSOR × HEADER | `GET /events`<br>DATA | 无 | CURSOR<br>`X-Cursor(HEADER)` `limit(QUERY)` | `data` | `event_id` | 73 |
| **cursor-body** | CURSOR × BODY（E3-D1 修复后的能力） | `POST /events/search`<br>DATA | 无 | CURSOR<br>`cursor(BODY)` `limit(BODY)` | `data` | `event_id` | 73 |
| **next-url** | NEXT_URL：同源绝对 / 相对 / 跨主机三种 | `GET /suppliers`<br>DATA | 无 | NEXT_URL<br>`next / ABSOLUTE_SAME_ORIGIN` | `data` | `id` | 41 |
|  |  | `GET /suppliers-relative`<br>DATA |  | NEXT_URL<br>`next / RELATIVE` | `data` | `id` | 41 |
|  |  | `GET /suppliers-crosshost`<br>DATA |  | NEXT_URL<br>`next / ABSOLUTE_CROSS_ORIGIN` | `data` | `id` | 41 |
| **body-page** | PAGE_NUMBER × BODY + 请求体值类型保真 | `POST /products/query`<br>DATA | 无 | PAGE_NUMBER<br>`page(BODY)` `size(BODY)` `起始 1` | `data.items` | `sku` | 45 |
| **next-link-only** | 只能跟随响应里 next 完整地址翻页、无任何分页参数 → 应标记为不支持 | `GET /records`<br>DATA | 无 | NEXT_URL<br>`next / ABSOLUTE_SAME_ORIGIN` | `data` | `id` | 35 |

#### C 请求与响应

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **echo** | Query / 请求体编码与类型保真验证 | `GET /echo`<br>DATA | 无 | NONE | `data.items` | `id` | 1 |
|  |  | `POST /echo-body`<br>DATA |  | NONE | `data.items` | `id` | 1 |
| **single-object** | 记录路径 × 单对象 → 1 条记录 | `GET /profile`<br>DATA | 无 | NONE | `data` | `id` | 1 |
| **biz-code** | 成功条件 EQ/NE × 数值/字符串 × 多允许值 | `GET /ok-zero`<br>DATA | 无 | NONE | `data.items` | `id` | 3 |
|  |  | `GET /ok-200`<br>DATA |  | NONE | `data.items` | `id` | 3 |
|  |  | `GET /ok-string`<br>DATA |  | NONE | `data.items` | `id` | 3 |
|  |  | `GET /biz-fail`<br>DATA |  | NONE | `data.items` | `id` | 0 |
| **nested** | 对象字段展开 + 嵌套数组子表（子表复制父字段） | `GET /orders`<br>DATA | 无 | NONE | `data.items` | `order_no` | 12 |
| **required-business-param** | 验证连接前检查请求值是否齐全：路径参数与 required Query 无固定值 / 无依赖 / 无测试值时返回 NEEDS_TEST_VALUES 且不发请求 | `GET /customers/{customerId}`<br>DATA | HEADER `X-Api-Key` = `test-key-123` | NONE | `data.items` | `id` | 1 |
| **dotted-param** | Query 参数名含点号（filter.name / page.size）时按原名透传，不拆成嵌套对象 | `GET /items`<br>DATA | HEADER `X-Api-Key` = `test-key-123` | NONE | `data.items` | `id` | 3 |
| **shared-params** | 公共参数识别：components.parameters + $ref（操作级 / 路径项级）；内联的同名或重复参数不应被当成公共参数 | `GET /orders`<br>DATA | 无 | NONE | `data.items` | `id` | 3 |
|  |  | `GET /customers`<br>DATA |  | NONE | `data.items` | `id` | 3 |
|  |  | `GET /products`<br>DATA |  | NONE | `data.items` | `id` | 3 |
|  |  | `GET /warehouses`<br>DATA |  | NONE | `data.items` | `id` | 3 |
| **root-object-record** | 记录路径 = 根对象 → 1 条记录；标量数组与对象数组保留为字段 | `GET /order`<br>DATA | 无 | NONE | 根（空串） | `orderNo` | 1 |
| **form-body-post** | 业务接口请求体为 application/x-www-form-urlencoded → 应标记为不支持 | `POST /orders/search`<br>DATA | 无 | NONE | `data.items` | `id` | 6 |
| **nested-two-level** | 记录里的对象数组的元素再带一层对象数组，两层都有可作主键的字段 | `GET /orders`<br>DATA | 无 | NONE | `data.items` | `order_no` | 3 |
| **special-field-names** | 数组字段及其子字段的名字含连字符、中文、以数字开头 | `GET /products`<br>DATA | 无 | NONE | `data.items` | `id` | 3 |

#### D 依赖

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **depend-path** | 前置依赖映射 × PATH；逐父记录条数不同 | `GET /products`<br>DATA | 无 | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `sku` | 30 |
|  |  | `GET /products/{sku}/batches`<br>DATA |  | NONE | `data` | `batch_no` | 60 |
| **depend-body** | 前置依赖映射 × BODY | `GET /customers`<br>DATA | 无 | NONE | `data.items` | `id` | 12 |
|  |  | `POST /customer-orders/query`<br>DATA |  | NONE | `data.items` | `order_no` | 18 |
| **required-header-cookie** | 业务参数位置 HEADER / COOKIE 且必填、无默认值；可绑定到前置接口的字段 | `GET /tenants`<br>DATA | 无 | NONE | `data.items` | `id` | 3 |
|  |  | `GET /invoices`<br>DATA |  | NONE | `data.items` | `invoice_no` | 6 |
|  |  | `GET /quotas`<br>DATA |  | NONE | `data.items` | `quota_id` | 4 |
| **depend-empty-parent** | 依赖展开时前置接口成功但没有记录；下游直接带合法取值仍可正常调用 | `GET /categories`<br>DATA | 无 | NONE | `data.items` | `id` | 0 |
|  |  | `GET /categories/{categoryId}/products`<br>DATA |  | NONE | `data.items` | `product_id` | 0 |
| **depend-mixed-key** | 前置接口同一个取值字段有的记录是数字、有的是文本；只差类型的取值如何发起下游请求；一个下游的两个参数分别取自两个前置接口 | `GET /shops`<br>DATA | 无 | NONE | `data.items` | `id` | 4 |
|  |  | `GET /shops/{ref}/staff`<br>DATA |  | NONE | `data.items` | `staff_no` | 5 |
|  |  | `GET /seasons`<br>DATA |  | NONE | `data.items` | `code` | 2 |
|  |  | `GET /sales`<br>DATA |  | NONE | `data.items` | `sale_id` | 6 |

#### E 故障

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **faults** | 错误处理策略、重试、限流、超时、非 JSON、截断、结构漂移、重定向 | `GET /always-500`<br>FAULT | 无 | NONE | `data.items` | `id` | — |
|  |  | `GET /flaky`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /biz-error`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /rate-limit`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /slow`<br>FAULT |  | NONE | `data.items` | `id` | 5 |
|  |  | `GET /not-json`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /huge`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /drifted`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /redirect`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /ok`<br>FAULT |  | NONE | `data.items` | `id` | 5 |
| **depend-switchable** | 分支依赖 A→B→C、A→D；按接口切换正常 / 持续失败，验证连续失败暂停与修复后恢复 | `GET /customers`<br>DATA | 无 | NONE | `data.items` | `id` | 5 |
|  |  | `GET /customers/{customerId}/orders`<br>DATA |  | NONE | `data.items` | `order_no` | 11 |
|  |  | `GET /orders/{orderNo}/items`<br>DATA |  | NONE | `data.items` | `item_no` | 16 |
|  |  | `GET /addresses`<br>DATA |  | NONE | `data.items` | `address_id` | 6 |
| **depend-two-roots** | 下游的两个参数分别取自两个互不依赖的根接口；三个接口各自可切换正常 / 持续失败 | `GET /warehouses`<br>DATA | 无 | NONE | `data.items` | `id` | 3 |
|  |  | `GET /periods`<br>DATA |  | NONE | `data.items` | `code` | 2 |
|  |  | `GET /stock`<br>DATA |  | NONE | `data.items` | `stock_id` | 12 |
| **page-fail-midway** | 页码分页取到第 3 页起失败；修复（切开关）后可完整取完 | `GET /records`<br>DATA | 无 | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `id` | 50 |

#### F 类型不一致

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **type-mismatch** | 验证接口时把真实响应与已保存字段类型做兼容性比对：文档声明与实际返回故意不一致（全项目唯一，含 2 个不该判不兼容的负例与 1 个对照组） | `GET /number-as-string`<br>DATA | 无 | NONE | `data.items` | `id` | 6 |
|  |  | `GET /string-as-number`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /bool-as-string`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /bool-as-string-consistent`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /datetime-as-epoch`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /object-as-string`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /int-vs-decimal`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /nullable-mostly-null`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /consistent`<br>DATA |  | NONE | `data.items` | `id` | 6 |

<!-- SCENARIO-TABLE:END -->

### 文档格式与版本

- **版本**：所有正常场景的文档都是 **OpenAPI 3.0.3**。产品口径不支持 Swagger 2.0、也不支持 OpenAPI 3.1 及以上，
  所以 `apikey-header` / `page-number` 不再提供 `/swagger2.json`（2026-10-08 起，清单里的 `swagger2Url` 一并移除）。
- **三种扩展名**：每个场景 `/s/<场景>/openapi.json` / `.yaml` / `.yml`，合并文档 `/openapi/<鉴权类型|all>.json` / `.yaml` / `.yml`，
  静态目录 `openapi/` 同样每种类型三个文件。清单里 `scenarios[].openapiUrls` 与 `openapiBundleUrls` 给出三个地址
  （`openapiUrl` / `openapiBundles` 保留，分别指 `.json` 与 `.yaml`）。
- **同源生成，不存在三份各自维护**：在线的三种地址输出的是**同一个文档对象**（`build_openapi` / `build_bundle` 的结果），
  只是按扩展名选 JSON 或 YAML 序列化，`.yaml` 与 `.yml` 逐字节相同；静态文件由 `tools/export_openapi_by_auth.py`
  对同一个对象一次写出三个文件。`tests/test_docs.py` 的 `DocFormatTest` 与 `tests/test_bundle.py` 的 `StaticFormatsTest`
  守着三者等价。
- **反例文档（应被后端拒绝）**：不是正常场景，不计入场景数，不进合并文档；清单在 `negativeDocuments`
  （`expectedBackendSupport = REJECTED`）。

  | 文档 | 版本 | 推荐地址 | 说明 |
  |---|---|---|---|
  | `swagger2` | Swagger 2.0 | `/__negative/swagger2.json` | 另有 `.yaml` / `.yml` |
  | `openapi31` | OpenAPI 3.1.0 | `/__negative/openapi31.yaml` | 另有 `.yml` / `.json` |

  两份文档描述同一个最简接口 `GET /__negative/items`（真实可请求，恒定 3 条），由同一份 3.0.3 文档转换而来。
  后端若没有拒绝而真的发了请求，`GET /__admin/requests?scenario=__negative` 查得到。

### 几个需要注意的语义

| 端点 | 行为 |
|---|---|
| `page-number` `/products-nosize` | **带了 `page_size` 返回 400**（不是静默忽略），响应无 `total`，靠空页结束 |
| `body-page` `/products/query` | `page` / `size` 必须是 JSON 数值，写成字符串返回 400 |
| `cursor-body` `/events/search` | `limit` 必须是 JSON 数值 |
| `next-url` `/suppliers-crosshost` | `next` 指向 `http://localhost:<端口>`，与文档里的 `127.0.0.1` 不同源，**后端应拒绝跟随**；拒绝时只应取到首页 15 条（`expectedTotal` 给的是数据总量 41） |
| `oauth2-post` `/oauth/token` | 带了 `Authorization` 头返回 400（证明后端选对了 client_secret_post） |
| `token-json-body` `/auth/token` | **只接受 `application/json`**，表单或其它 Content-Type 返回 415；Token 在 `data.token.value`、有效期在 `data.token.expiresIn`（恒为 7200，令牌不会真的过期） |
| `token-form-body` `/auth/token` | **只接受 `application/x-www-form-urlencoded`**，JSON 返回 415；Token 在顶层 `tokenValue`（不是 `access_token`） |
| `shared-params` `/orders` `/customers` | 3 个公共参数（`X-Tenant-Id` 头、`locale`、`apiVersion`）定义在 `components.parameters`，`/orders` 在**操作级**、`/customers` 在**路径项级**用 `$ref` 引用；缺任一个、`locale` 不在 `zh-CN` / `en-US`、`apiVersion` 非整数都返回 400。两个接口各自内联的 `keyword` 不是公共参数 |
| `shared-params` `/products` `/warehouses` | `/products` 只引用 `locale`；`/warehouses` 不引用任何公共参数，它内联的 `locale` 与公共参数同名但可选、无枚举、**不校验**（传 `fr-FR` 也 200）。清单的 `sharedParameters` 写明每个端点的引用位置 |
| `next-link-only` `/records` | 文档不声明任何参数，只能原样请求响应里 `next` 的完整地址；带 `page` / `page_size` / `size` / `offset` / `limit` / `cursor` 任一参数返回 400。清单 `expectedBackendSupport = UNSUPPORTED` |
| `root-object-record` `/order` | 响应根就是一个对象（无包裹层），`recordPath` 为空串；应作为 1 条记录，`tags`（标量数组）与 `lines`（对象数组）保留为字段，见清单 `arrayFields` |
| `form-body-post` `/orders/search` | 业务接口的请求体只声明并只接受表单，JSON 返回 415。清单 `expectedBackendSupport = UNSUPPORTED` |
| `oauth2-basic` `/oauth/token` | 表单体里出现 `client_secret` 返回 400；签发的令牌**按使用次数失效**，默认第 3 次业务请求返回 401 |
| `oauth2-expiring` `/oauth/token` | 令牌**按时间过期**：`expires_in` 返回实际有效秒数（默认 40），到点后业务请求 401。用于验证后端「距过期不足 30 秒提前重取」——拿到令牌后等约 15 秒再发请求，应看到令牌端点被请求 2 次且业务请求无 401 |
| `oauth2-extra` `/oauth/token` | 令牌请求带了附加鉴权参数 `X-App-Key` 返回 400（后端约定：令牌请求不带附加参数） |
| `oauth2-paged-maxuses` `/items` | 页码分页 50 条（5 页），令牌**最多用于 2 次业务请求**。期望：一次完整同步 5 次成功的业务请求 → 令牌端点被请求 **3** 次、业务请求出现 **2** 次 401 且每次之后紧跟一次**同页重放**成功、最终 50 条无重复 |
| `oauth2-paged-expiring` `/items` | 同上 50 条 5 页，令牌按时间过期（默认 **35** 秒），且**每个业务请求服务端固定延迟 6 秒**（`pageDelaySeconds`）。期望：令牌端点被请求 **2** 次、**5 次业务请求全部 200、无任何 401**、最终 50 条无重复。参数是按后端的复用窗口算的，见下方 |
| `apikey-header-prefix` | `Authorization` 的值必须是 `Token test-key-123`，无前缀或前缀不符 401，错误消息里点名「缺少前缀 Token」 |
| `forbidden` `/secret-items` | 凭证正确也恒定 403；无凭证仍是 401 |
| 所有场景 | `PUT` / `PATCH` / `DELETE` 一律 405 + `Allow: GET, POST` |

> ⚠️ **`type-mismatch` 场景的文档是故意写错的，别当 bug 修**
>
> 全项目只有这一个场景的 OpenAPI 与实现**刻意不一致**：其余场景的 schema 都由「真的调一次
> 处理函数」推断出来，天然等于实现；这个场景显式手写 schema 覆盖推断结果。
>
> 它存在的理由：后端「验证接口」时会把真实响应与已保存的字段类型做兼容性比对，不兼容就在
> 验证阶段报告，而不是等首次同步才失败。这里给它一个能稳定触发的对端。
>
> 9 个端点 = **5 个正例**（数值声明成字符串、字符串声明成数值、布尔声明成字符串、
> date-time 实际返回毫秒时间戳、object 实际返回 JSON 文本）+ **2 个负例**
> （`int-vs-decimal`：整数声明 vs 小数实际；`nullable-mostly-null`：6 条里 5 条 null）
> + **2 个对照组**（`/consistent` 全字段一致；`/bool-as-string-consistent` 见下）。
> 两个负例**预期不判不兼容**，是用来挡住「把类型比对做得过于严格」的。
>
> **布尔专用对照**：`/bool-as-string` 与 `/bool-as-string-consistent` 用**同一份文档声明**
> （`active: boolean`），前者实际返回字符串、后者返回真布尔。用来复现「字段被判为 BOOLEAN，
> 接口实际给字符串，写进中间库后下游 Flink 读取报 `String cannot be cast to Boolean`」——
> 真布尔时下游正常、字符串时出问题。`/bool-as-string` 的 6 条取值依次是
> `"true"` `"false"` `"1"` `"0"` `"true"` `"false"`，**四种常见的布尔字符串写法都覆盖到**，
> 因为「字符串转布尔」的兼容逻辑常常只认其中一种。
>
> 每个端点在 `/__admin/scenarios` 里带 `declaredVsActual`，E2E 直接取它当期望：
> `[{field, declaredType, declaredFormat, actualJsonType, expectIncompatible}]`。
> 这张表两侧都有自测正面钉着（文档里确实那么声明、响应里确实那么返回），不会悄悄失真。

> **为什么 `oauth2-paged-expiring` 是 ttl 35 秒 + 每页延迟 6 秒**
>
> 后端的令牌复用窗口不是简单的「过期前 30 秒」：**有效期 ≤60 秒的短效令牌按一半有效期复用**
> （ttl=35 → 17.5 秒后才重取），有效期 >60 秒的长效令牌才走「过期前 30 秒」。
>
> 延迟 6 秒时，5 页的请求发起时刻是 **0 / 6 / 12 / 18 / 24 秒**：第 4 页发起前已过 18 秒
> （> 17.5 秒的复用窗口），后端应**主动重取**；而全程 30 秒 < 有效期 35 秒，
> 所以**即使后端不重取也不会出现 401**。正因如此，「令牌端点 2 次 + 全程无 401」
> 才能干净地证明是主动重取，而不是撞了 401 之后重放救回来的。
>
> **别把 `oauth2PagedTtlSeconds` 调过 60**——那会切到长效令牌的规则，这个场景就观察不到目标现象了。
> 早先的默认值是每页 3 秒（发起时刻 0/3/6/9/12 秒，全在复用窗口内），后端不重取是正确行为，
> 场景等于白跑。

### 依赖类的条数公式

| 子接口 | 公式 | 取值 | 合计 |
|---|---|---|---|
| `depend-path` `GET /products/{sku}/batches` | `(序号 * 7) % 5`，序号取自 `DPD-000NN` | 0~4 条 | 30 个商品共 **60** 条 |
| `depend-body` `POST /customer-orders/query` | `(序号 * 3) % 4`，序号取自 `CUS-000NN` | 0~3 条 | 12 个客户共 **18** 条 |
| `depend-switchable` `GET /customers/{customerId}/orders` | `序号 % 3 + 1`，序号取自 `SWC-000NN` | 1~3 条 | 5 个客户共 **11** 条 |
| `depend-switchable` `GET /orders/{orderNo}/items` | `(客户序号 + 订单行号) % 2 + 1`，取自 `SWC-000NN-O<行号>` | 1~2 条 | 11 张订单共 **16** 条 |
| `depend-switchable` `GET /addresses?customer_id=` | `(序号 * 2) % 3`，序号取自 `SWC-000NN` | 0~2 条 | 5 个客户共 **6** 条 |
| `required-header-cookie` `GET /invoices`（请求头 `X-Tenant-Id`） | 三个租户依次 `2 / 1 / 3` | 1~3 条 | 3 个租户共 **6** 条 |
| `required-header-cookie` `GET /quotas`（Cookie `region`） | 三个区域依次 `1 / 2 / 1` | 1~2 条 | 3 个区域共 **4** 条 |
| `depend-empty-parent` `GET /categories/{categoryId}/products` | 每个合法取值恒定 2 条 | 2 条 | 前置 0 条，按依赖展开共 **0** 条 |
| `depend-mixed-key` `GET /shops/{ref}/staff` | 取值 `1` → 2 条、`2` → 1 条、`3` → 2 条 | 1~2 条 | 3 个不同取值共 **5** 条（参考值） |
| `depend-mixed-key` `GET /sales?shop_ref=&season=` | 每个（取值, 季）组合恒定 1 条 | 1 条 | 两两组合共 **6** 条（参考值） |
| `depend-two-roots` `GET /stock?warehouse_id=&period=` | 每个（仓库, 期间）组合恒定 2 条 | 2 条 | 两两组合共 **12** 条（参考值） |

前 5 个父记录的条数分别是 `2 / 4 / 1 / 3 / 0` 与 `3 / 2 / 1 / 0 / 3`——有 0 条的父记录，用来验证空结果不算失败。
`sku` 不存在返回 404；`customerId` 缺失返回 400，不存在则返回空列表。

### 可切换故障的分支依赖（`depend-switchable`）

四个读取接口构成分支依赖 **A→B→C、A→D**，用来验证「接口连续失败后暂停、修好后重新同步恢复」：

| 接口 | 依赖 | 正常时的条数 |
|---|---|---|
| A `GET /customers` | — | 5（`SWC-00001` ~ `SWC-00005`） |
| B `GET /customers/{customerId}/orders` | 路径参数取 A 的 `id` | 各客户 `2 / 3 / 1 / 2 / 3`，共 11 |
| C `GET /orders/{orderNo}/items` | 路径参数取 B 的 `order_no` | 共 16 |
| D `GET /addresses?customer_id=` | 查询参数取 A 的 `id` | 各客户 `2 / 1 / 0 / 2 / 1`，共 6 |

- 主键都可预测：订单号 `<客户 id>-O<行号>`、明细号 `<订单号>-I<行号>`、地址号 `<客户 id>-A<行号>`；同一个父记录每次返回的子记录完全相同。
- 每个接口有一个**开关键**（清单里的 `faultKey`：`customers` / `orders` / `items` / `addresses`），由
  `/__admin/faults?scenario=depend-switchable` 在运行期切换，不用重启、不影响别的场景：

  ```bash
  # 把 B 切到持续失败（status 可省，缺省 503，可设 400~599）
  curl -s -X POST 'http://127.0.0.1:18090/__admin/faults?scenario=depend-switchable' \
       -H 'Content-Type: application/json' -d '{"endpoint":"orders","mode":"fail","status":503}'
  # 一次切多个
  curl -s -X POST 'http://127.0.0.1:18090/__admin/faults?scenario=depend-switchable' \
       -H 'Content-Type: application/json' \
       -d '{"switches":[{"endpoint":"customers","mode":"fail"},{"endpoint":"orders","mode":"normal"}]}'
  # 查看开关与调用次数
  curl -s 'http://127.0.0.1:18090/__admin/faults?scenario=depend-switchable'
  ```

- **持续失败**是指一直返回该状态码与 JSON 错误体（`{"code": 503, "message": "..."}`），直到切回 `normal`；
  与 `faults` 的 `/flaky`（按调用次数失败、之后自动成功）不同。处在持续失败时先于参数校验返回。
- `GET` 返回每个接口的 `mode` / `failStatus` 与 `calls`（`total` / `succeeded` / `failed`）：自上次复位以来的调用次数，
  非 2xx / 3xx 的响应（含被开关拦下的、404、400）都计入 `failed`。切开关不清计数；取文档不计数。
- 复位：`POST /__admin/reset?scenario=depend-switchable` 把四个开关还原为正常并把计数归零；
  不带场景名的全量复位同样会还原。复位别的场景不动它。
- 文档与开关状态无关：生成文档时跳过开关与计数，接口处在持续失败时取到的文档也不变。

### H-VERIFY-1013 补测场景（10 个）

为了在页面上补验「只有用例为证」的事项而加，都是现有场景造不出条件的情形。

**鉴权（A 组）**

- `token-typed-fields`：鉴权同 `bearer-request`（同一组账号密码，令牌放 `Authorization: Bearer`）。
  `POST /login` 的返回除 `data.accessToken`（文本）、`data.expiresIn`（整数 3600）、`data.tokenType`（文本）外，
  多了布尔字段 `success`（顶层）、`data.refreshable` 与小数字段 `data.refreshRatio`（0.8）。
  清单 `tokenFlow.nonTextTokenResponseFields` 按类型列出这些字段。用于验「有效期路径的候选不包含布尔字段」。
- `oauth2-scope-required`：客户端凭证同 `oauth2-post`（`client_secret_post`）。令牌接口 `POST /oauth/token`
  **只接受 `scope=read`**：不带、为空、带别的值（`write`、`read write`、`READ`）一律 400，错误体
  `{"error": "invalid_scope", "error_description": "..."}`。校验顺序是 `grant_type` → 客户端凭证（错了 401
  `invalid_client`）→ `scope`。业务接口 `/items` 只校验令牌，不再看 scope。其余 6 个 `oauth2-*` 场景仍然不校验 scope。
- `token-slow`：鉴权同 `bearer-request`。`POST /login` 校验通过后**延迟 5 秒**再返回（返回形状与 `bearer-request` 相同）；
  账号密码错误立即 401、不延迟；业务接口与取文档不延迟。秒数是配置项 `tokenSlowDelaySeconds`（0~60），
  `/__admin/config` 可改，清单 `tokenFlow.tokenDelaySeconds` 反映当前值。

**请求与响应（C 组）**

- `nested-two-level`：`GET /orders` 返回 3 条订单（主键 `order_no`）。每条订单有对象数组 `lines`（主键 `line_no`，
  各订单 `2 / 3 / 1` 共 6 条），每个 `lines` 元素里再有对象数组 `batches`（主键 `batch_no`，每行 1~2 条共 9 条）。
  两层主键全局唯一。清单 `nestedArrays` 给出两层的路径、主键与条数。
- `special-field-names`：`GET /products` 返回 3 条（主键 `id`）。每条记录有三个对象数组字段，字段名与子字段名
  各占连字符 / 中文 / 数字开头三种情形（每个数组的第一个子字段是元素主键，每个数组 2 条元素、共 6 条）：

  | 数组字段 | 子字段 |
  |---|---|
  | `sku-list` | `sku-code`、`库存`、`1st_price` |
  | `规格列表` | `spec-name`、`取值`、`2nd_unit` |
  | `3d_models` | `model-id`、`格式`、`3d_size` |

**依赖（D 组）**

- `required-header-cookie`：前置 `GET /tenants`（3 条，`id` 为 `RHC-00001` ~ `RHC-00003`，`region` 各不相同）。
  `GET /invoices` 需要**必填请求头** `X-Tenant-Id`（取自 `/tenants` 的 `id`），`GET /quotas` 需要**必填 Cookie**
  `region`（取自 `/tenants` 的 `region`）。文档里两个参数都是 `required: true`，不给 `default` 与 `example`；
  缺失或为空返回 400，放错位置（比如把 Cookie 参数放进 Query）也算缺失；不认识的取值返回空列表。
- `depend-empty-parent`：前置 `GET /categories` **恒定返回 0 条**。因为空数组推断不出字段，它的记录结构
  （`id`、`name`）在 schema 里显式声明，文档的响应示例带一条示意记录——**示例只为示意字段，真实响应没有记录**。
  下游 `GET /categories/{categoryId}/products` 直接带合法取值（`DEP-CAT-00001` ~ `DEP-CAT-00003`，
  清单 `validPathValues`）调用时各返回 2 条，其它取值 404。
- `depend-mixed-key`：前置 `GET /shops` 的取值字段 `ref` 依次是数字 `1`、文本 `"1"`、数字 `2`、文本 `"3"`
  ——**数字 1 与文本 "1" 是同一个取值的两种类型**，文档里 `ref` 声明为 `oneOf` 整数 / 文本。
  `GET /shops/{ref}/staff` 按取值返回（`1` → 2 条、`2` → 1 条、`3` → 2 条，其它 404）：3 个不同取值各调一次共 5 条；
  若按 4 条父记录各调一次共返回 7 条，其中取值 `1` 的 2 条主键重复。清单 `referenceTotals` 两个数都给。
  另有前置 `GET /seasons`（`code` 为 `S1` / `S2`）与下游 `GET /sales?shop_ref=&season=`：两个必填查询参数分别取自
  `/shops` 的 `ref` 与 `/seasons` 的 `code`，缺任一个 400，每个组合 1 条。

**故障（E 组，带开关）**

- `depend-two-roots`：两个互不依赖的根 `GET /warehouses`（3 条，主键 `id`）、`GET /periods`（2 条，主键 `code`），
  下游 `GET /stock?warehouse_id=&period=` 的两个必填查询参数分别取自它们，缺任一个 400（消息写明缺哪个），
  每个组合恒定 2 条。三个接口各有一个开关（键 `warehouses` / `periods` / `stock`），缺省正常，
  用法与 `depend-switchable` 完全相同，入口是 `/__admin/faults?scenario=depend-two-roots`。
- `page-fail-midway`：**不是正常场景**。`GET /records` 页码分页（`page` / `page_size`，共 50 条 5 页），
  **缺省状态下第 1、2 页正常，第 3 页起恒定返回 500**，一次完整同步必然中途失败（失败前取到 20 条）。
  开关键 `records`，入口 `/__admin/faults?scenario=page-fail-midway`：

  ```bash
  # 切到 normal：5 页全部正常（验修复后恢复）
  curl -s -X POST 'http://127.0.0.1:18090/__admin/faults?scenario=page-fail-midway' \
       -H 'Content-Type: application/json' -d '{"endpoint":"records","mode":"normal"}'
  # 切回第 3 页起失败（status 可设 400~599，缺省 500）
  curl -s -X POST 'http://127.0.0.1:18090/__admin/faults?scenario=page-fail-midway' \
       -H 'Content-Type: application/json' -d '{"endpoint":"records","mode":"fail"}'
  ```

  它与另两个带开关的场景相反：**缺省是失败**，`POST /__admin/reset?scenario=page-fail-midway` 与全量复位都会回到
  「第 3 页起失败」。清单的 `expectedTotal`（50）是切到 normal 之后的条数，另有 `rowsBeforeFailure`（20）。

上面依赖类场景里标「参考值」的合计，只说明「服务对每种参数取值各返回什么」时按某种调用方式算出来的数，
**不代表后端应该怎么组合、去重**——那正是要验的东西。

### 记录的字段形态

每条记录固定覆盖七种形态，供后端的字段类型推断 / 对象展开 / 主键解析取材：

```json
{
  "id": "PRD-00001",            // 字符串（主键；offset 场景刻意没有这个字段）
  "name": "PRD物料1",            // 字符串（含中文）
  "category": "整机",
  "price": 11.27,               // 小数
  "quantity": 13,               // 整数
  "active": true,               // 布尔
  "updated_at": "2026-01-01T09:00:00+08:00",   // ISO 时间字符串
  "remark": null,               // 每 5 条一个 null（前 5 条里必有非 null，保证类型可推断）
  "spec": { "length": 11.5, "width": 6.25, "unit": "cm" }   // 嵌套对象
}
```

## admin 接口

均无鉴权，且**不写进任何 OpenAPI 文档、也不记进请求日志**。

| 接口 | 说明 |
|---|---|
| `GET /health` | `{"status":"ok"}` |
| `GET /__admin/scenarios` | 全部场景清单：文档地址（`openapiUrl` 与三种扩展名的 `openapiUrls`）、顶层 `negativeDocuments`（反例文档）、鉴权方式与测试凭证（含 `prefix` / `extraAuthParams` / `tokenFlow`）、每个端点的 `method` / `path` / `role` / `paginationType` / 分页参数名与位置 / `recordPath` / `primaryKey` / `expectedTotal` / `sampleFirst3` |
| `GET /__admin/requests?scenario=<名字>&limit=<N>` | 服务端实际收到的请求日志，按时间顺序；含序号、方法、路径、解码后的 Query、`rawQuery`、全部请求头、Cookie、原始请求体、响应状态码。内存最多保留最近 5000 条 |
| `POST /__admin/reset` | 全量复位：清空请求日志，复位 `flaky` / `rate-limit` 计数器、全部带开关场景（`depend-switchable` / `depend-two-roots` / `page-fail-midway`）的故障开关与调用计数、已签发令牌与令牌序号。**不改 config** |
| `POST /__admin/reset?scenario=<名字>` | 只复位该场景：清掉它的请求日志（其它场景的保留，序号不归零）、作废它签发的令牌；故障计数器只在 `scenario=faults` 时复位，故障开关与调用计数只在复位带开关的场景自己时还原（`page-fail-midway` 还原后是「第 3 页起失败」）。场景名不存在返回 404 |
| `GET/POST /__admin/config` | 读取 / 调整可变参数，改完 `/__admin/scenarios` 立刻反映新值。键与取值范围：`oauth2BasicTokenMaxUses`（1~1000，默认 2）、`oauth2ExpiringTokenTtlSeconds`（1~3600，默认 40）、`oauth2PagedTokenMaxUses`（1~1000，默认 2）、`oauth2PagedTtlSeconds`（1~3600，默认 35）、`oauth2PagedDelaySeconds`（**0**~60，默认 6；0 表示不延迟，自测靠它跑快）、`tokenSlowDelaySeconds`（**0**~60，默认 5；`token-slow` 取令牌接口的延迟秒数） |
| `GET /__admin/faults?scenario=<场景>` | 带开关的场景有三个：`depend-switchable`、`depend-two-roots`、`page-fail-midway`。返回该场景各接口的故障开关（`mode` / `failStatus`）与自上次复位以来的调用次数（`calls.total` / `succeeded` / `failed`） |
| `POST /__admin/faults?scenario=<场景>` | 切换开关：体为 `{"endpoint": 开关键, "mode": "normal" \| "fail", "status": 状态码}` 或 `{"switches": [...]}`；`status` 缺省 503（`page-fail-midway` 缺省 500）、可设 400~599；有一项不合法整体返回 400。缺 `scenario` 返回 400，场景没有开关返回 404 |
| `GET /__admin/state` | 当前配置、故障计数器、`depend-switchable` 的故障开关（`faultSwitches`）、全部带开关场景按场景名分组的开关（`faultSwitchesByScenario`）、已签发令牌快照（排查用） |
| `GET /openapi/<鉴权类型\|all>.yaml` / `.yml` / `.json` | 按鉴权类型合并的 OpenAPI 文档（见上文），同样不记日志；`/__admin/scenarios` 的 `openapiBundles`（`.yaml`）与 `openapiBundleUrls`（三种扩展名）列出全部地址 |

鉴权描述的结构（`/__admin/scenarios` → `scenarios[].auth`）：

```jsonc
{
  "type": "oauth2",                       // none / bearer / apiKey / basic / oauth2 / fixedParam
  "credentials": [
    {"in": "HEADER", "name": "Authorization", "testValue": "<由令牌接口签发>", "prefix": "Bearer"}
  ],
  "extraAuthParams": [{"name": "X-App-Key", "in": "HEADER", "value": "extra-key-1"}],
  "tokenFlow": {
    "kind": "OAUTH2_CLIENT_CREDENTIALS",  // 或 REQUEST_TOKEN
    "method": "POST",
    "url": "http://127.0.0.1:18090/s/oauth2-post/oauth/token",
    "grantType": "client_credentials",
    "clientAuthMethod": "post",           // post / basic
    "clientId": "test-client", "clientSecret": "test-secret", "scope": "read",
    "tokenPath": "access_token",
    "inject": {"in": "HEADER", "name": "Authorization", "prefix": "Bearer"}
  }
}
```

`REQUEST_TOKEN` 的 `tokenFlow` 另有 `contentType`（`application/json` 或 `application/x-www-form-urlencoded`，
决定 `requestBody` 按 JSON 还是按表单提交）、`requestBody`（登录体）、`tokenPath`（如 `data.accessToken`、
`data.token.value`、`tokenValue`）、`expiresInPath`（如 `data.expiresIn`）。

## 如何新增一个场景

1. 在 `scenarios/` 下挑一个模块（或新建），写一个 `_scenario_xxx()`：

   ```python
   def _scenario_demo():
       def handler(ctx):
           return json_resp({"code": 0, "data": {"items": ROWS}})

       ep = Endpoint(
           "GET", "/items", handler,
           operation_id="listDemoItems",        # 读语义词，便于后端判为读取接口
           summary="查询演示数据",
           record_path="data.items", primary_key="id",
           dataset=lambda: list(ROWS),          # expectedTotal / sampleFirst3 由它算
           pagination={"type": "NONE"},
           doc_sample={},                       # 生成文档时用什么参数调这个处理函数
       )
       return Scenario("demo", "演示", "覆盖的后端能力", [ep], auth=NO_AUTH)
   ```

2. 把它加进该模块的 `build_scenarios()`；`scenarios/__init__.py` 会自动收进 `REGISTRY`
   （名字重复会在启动时报错）。
3. 需要鉴权就传 `auth=AuthSpec(kind=..., requirements=[...])`；要附加鉴权参数用
   `extra_param_req(...)`，它会自动进清单的 `extraAuthParams`。
4. 有副作用的端点（计数、sleep、签发令牌）必须显式给 `response_schema`，并把 `doc_sample` 设为 `None`。
   只需要跳过副作用、仍想让 schema 由真实响应推断的（如 `/slow`、带开关的场景），改为在处理函数里判断 `ctx.doc_mode`。
   要给接口加「正常 / 持续失败」的开关与调用计数：用 `scenarios/faultswitch.py` 的 `SwitchBoard`，
   `faultswitch.register(...)` 登记后 `/__admin/faults?scenario=<场景>`、按场景复位与全量复位自动生效
   （参照 `scenarios/faultcases.py`）。
5. 跑 `./run_tests.sh`——通用用例会自动覆盖新场景（文档可达性、清单一致性、翻页到底）。
   同步数量断言：`tests/test_faults_admin.py`（场景总数）、`tests/test_docs.py`（端点总数）；
   若新增的是 A 类鉴权场景或 B 类数据端点，还有 `tests/test_auth.py` /
   `tests/test_pagination.py`。
6. 重新生成场景总表：`python3 tools/dump_scenario_table.py`，把结果贴回本文件的
   `<!-- SCENARIO-TABLE -->` 区块。
   公共参数用 `Scenario(shared_parameters={...})` + `ref_param("组件名")` 声明，路径项级参数用 `Endpoint(path_parameters=[...])`。
7. 重新生成按鉴权类型的文档：`python3 tools/export_openapi_by_auth.py`（新的鉴权类型会自动多出一份 YAML；
   若是新的 `AuthSpec.kind`，顺手在 `scenarios/bundle.py` 的 `KIND_TITLES` 补中文名，并更新上文的文件表）。

## 已知局限

- 请求日志只在内存，进程退出即丢；最多 5000 条，超出丢最早的。
- **并发共享的现状**：请求日志已能按场景隔离清理（`POST /__admin/reset?scenario=<名字>`），
  不同场景的用例可以并发跑同一个实例而不互相清掉日志。但仍有两处是**进程级全局**：
  `/__admin/config` 的配置项（`oauth2BasicTokenMaxUses` / `oauth2ExpiringTokenTtlSeconds`）、
  以及故障场景那几个计数器（它们本就只属于 `faults`）。所以：
  **改配置或跑 `faults` 场景的用例必须独占实例或串行**，其余场景并发跑没问题。
  `depend-switchable`、`depend-two-roots`、`page-fail-midway` 的故障开关与调用计数也是进程级的（每个场景各一份）：两拨人同时用同一个场景会互相切开关，
  而且别人做不带场景名的全量复位会把正在「持续失败」的接口切回正常。
  （`page-fail-midway` 相反：复位会让它回到缺省的「第 3 页起失败」。）
- 令牌有两种失效方式：按**使用次数**（`oauth2-basic`、`oauth2-paged-maxuses`，可复现）与按**时间**
  （`oauth2-expiring`、`oauth2-paged-expiring`）。其余场景的令牌不失效，`expires_in` 恒为 3600。
- `oauth2-paged-expiring` 的每页延迟默认 6 秒（一次完整同步约 30 秒）：跑它的用例会慢，
  且**它与 `/__admin/config` 一样是进程级的**，并发跑别的用例时把延迟调成 0 会影响到它。
  建议这个场景的 E2E 独占一个实例。
- 只支持 HTTP，没有 HTTPS / mTLS 场景。
- `suppliers-crosshost` 默认仍用`localhost`与`127.0.0.1`的主机名差异构造；研发IP模式也会换成不同主机名，
  不应跟随这个链接。它是异源反例，不代表产品支持下一页链接分页；本机若按IP而不是主机名判同源，仍可能不触发拒绝。
- 故障类端点大多没有进 `expectedTotal` 体系（返回随调用次数变化），需逐条按上面的表断言；例外是 `/slow` 与 `/ok`，它们恒定返回 5 条真实记录，可以按 `expectedTotal` 断言。
- `not-json` / `huge` 这类端点的响应体不是常规结构，文档里给的是显式 schema 与示意性 example。

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-25 | 研发演示交付：支持显式对外HTTP(S)源地址，文档/令牌/分页地址统一且本地默认保持；新增白名单Linux打包、IP版文档生成、只读部署检查、Docker构建文件及25＋10分钟业务剧本。场景和数据不变，56场景/112端点；全套237项自测通过。本地无Docker，镜像未构建；线上部署和研发页面排练未执行。 |
| 2026-09-20 | v1：33 个场景 / 59 个端点 / 84 条自测用例。含 Commander 追加的 4 个鉴权场景（`apikey-header-prefix`、`apikey-extra`、`basic-extra`、`oauth2-extra`）与 `role` / 结构化鉴权配置要求。 |
| 2026-09-20 | H2：`POST /__admin/reset` 支持 `?scenario=` 按场景复位；新增 `oauth2-expiring`（令牌按时间过期，`expires_in` 默认 40 秒，可由 `/__admin/config` 调）；`/__admin/scenarios` 的 `tokenFlow` 改为实时取配置值。34 个场景 / 61 个端点 / 93 条自测。 |
| 2026-09-20 | H3：修复 `Transfer-Encoding: chunked` 请求体读不到的缺陷（JDK HttpClient 默认走分块，原来只认 `Content-Length`，令牌 / 登录请求体被读成空串）；新增 `oauth2-paged-maxuses` 与 `oauth2-paged-expiring` 两个分页 OAuth2 场景（各 50 条 5 页）；配置项改为按键校验取值范围。36 个场景 / 65 个端点 / 117 条自测。 |
| 2026-09-20 | H4：`oauth2-paged-expiring` 的默认每页延迟 3 秒 → **6 秒**（ttl 保持 35），使第 4 页发起前越过后端「短效令牌按一半有效期复用」的 17.5 秒窗口；期望改为「令牌端点 2 次、5 次业务请求全 200、无 401」，并在 README 写明该窗口规则。 |
| 2026-09-20 | H5：`faults` 的 `GET /slow` 由返回空 `items` 改为返回 **5 条**确定性记录（id 前缀 `SLOW-`，结构与其它列表端点一致），延迟行为不变；其 200 schema 改为由真实响应推断（生成文档时靠 `doc_mode` 跳过 sleep），清单补 `recordPath` / `primaryKey` / `expectedTotal: 5`。 |
| 2026-09-20 | H6：新增 `type-mismatch` 场景（8 个端点，5 正例 + 2 负例 + 1 对照组），**全项目唯一刻意让文档与实现不一致**的场景，供后端「验证接口时比对字段类型」使用；清单新增 `declaredVsActual`；`Endpoint` 新增通用的 `extra_catalog` 机制。37 个场景 / 73 个端点 / 127 条自测。 |
| 2026-09-22 | H7：`type-mismatch` 新增 `/bool-as-string-consistent`（与 `/bool-as-string` 同一份文档声明、实际返回真布尔），用于复现下游 `String cannot be cast to Boolean`；`/bool-as-string` 的取值扩到 `"true"` / `"false"` / `"1"` / `"0"` 四种写法。37 个场景 / 74 个端点 / 129 条自测。 |
| 2026-09-24 | H8：新增按鉴权类型合并的 OpenAPI 文档：`openapi/<鉴权类型>.yaml` 静态文件（`tools/export_openapi_by_auth.py` 生成）与在线 `/openapi/<鉴权类型>.yaml` / `.json`；附汇总说明 `openapi/README.md`；标准库 YAML 输出器；`/__admin/scenarios` 新增 `openapiBundles`。37 个场景 / 74 个端点 / 139 条自测。 |
| 2026-09-30 | H-SC1：新增 `required-business-param` 场景（C 组，API Key 请求头鉴权，仅 1 个端点 `GET /customers/{customerId}`），路径参数 `customerId` 与 required Query `tenant` 均无 example / default、也不来自前置接口，供后端「验证连接前缺值返回 NEEDS_TEST_VALUES 且不发请求」使用；缺 `tenant` 返回 400、未知客户 404；清单新增 `requiredTestValues`。38 个场景 / 75 个端点 / 145 条自测。 |
| 2026-10-08 | H-SC2：新增 `dotted-param` 场景（C 组，API Key 请求头鉴权，仅 1 个端点 `GET /items`），Query 参数名本身带点号：必填 `filter.name`、可选整数 `page.size`，均无 example / default；记录回显 `filterName` / `pageSize`，缺或空 `filter.name` 返回 400；清单给 `requiredTestValues`。39 个场景 / 76 个端点 / 151 条自测。 |
| 2026-10-08 | H-1008：按当日需求更新新增 6 个场景 / 11 个端点：`shared-params`（公共参数 `components.parameters` + `$ref`，操作级 / 路径项级两种引用位置，内联同名与重复参数作对照）、`token-json-body` / `token-form-body`（取 Token 接口只认一种请求体类型，另一种返回 415；Token 路径 `data.token.value` / 顶层 `tokenValue`）、`next-link-only`（只能跟随 `next` 完整地址翻页，35 条 4 页）、`root-object-record`（根对象即一条记录，含标量数组与对象数组）、`form-body-post`（表单请求体的业务 POST）；清单新增 `sharedParameters` / `arrayFields` / `expectedBackendSupport`。**文档格式**：所有文档提供 `.json` / `.yaml` / `.yml` 三种扩展名（同一文档对象序列化），清单新增 `openapiUrls` / `openapiBundleUrls`；`apikey-header` / `page-number` 不再提供 Swagger 2.0，清单移除 `swagger2Url`；新增不计入场景数的反例文档 `/__negative/swagger2.*` 与 `/__negative/openapi31.*`（清单 `negativeDocuments`）。45 个场景 / 87 个端点 / 179 条自测。 |
| 2026-10-11 | H-1011：新增 `depend-switchable` 场景（E 组，无鉴权，4 个端点）：分支依赖 A→B→C、A→D（`/customers` → `/customers/{customerId}/orders` → `/orders/{orderNo}/items`，`/customers` → `/addresses?customer_id=`，正常时 5 / 11 / 16 / 6 条）；新增 admin 接口 `GET/POST /__admin/faults?scenario=depend-switchable`，按接口在运行期切换「正常 / 持续失败」（状态码可设，默认 503），并返回每个接口自上次复位以来的调用次数（成功、失败分开）；按场景复位与全量复位都会还原开关与计数；`/__admin/state` 新增 `faultSwitches`；清单端点新增 `faultKey` / `dependsOn`。46 个场景 / 91 个端点 / 195 条自测。 |
| 2026-10-13 | H-VERIFY-1013：新增 10 个补测场景（21 个端点），现有场景的行为与单场景文档不变。A 组 `token-typed-fields`（取令牌返回带布尔 `success` / `data.refreshable` 与小数 `data.refreshRatio`）、`oauth2-scope-required`（令牌接口只接受 `scope=read`，否则 400 `invalid_scope`）、`token-slow`（取令牌延迟返回，新配置项 `tokenSlowDelaySeconds`，默认 5）；C 组 `nested-two-level`（两层嵌套对象数组，3 / 6 / 9 条）、`special-field-names`（数组字段与子字段名含连字符 / 中文 / 数字开头）；D 组 `required-header-cookie`（必填请求头 `X-Tenant-Id`、必填 Cookie `region`）、`depend-empty-parent`（前置恒定 0 条）、`depend-mixed-key`（取值字段依次为 1 / "1" / 2 / "3"，另有一个下游取自两个前置）；E 组 `depend-two-roots`（一个下游依赖两个根，三个接口各有开关）、`page-fail-midway`（缺省第 3 页起 500，切 normal 才全通）。`/__admin/faults` 从只认 `depend-switchable` 扩成按场景名分发到三个场景（原场景的请求与返回不变）；`/__admin/state` 新增 `faultSwitchesByScenario`；清单端点新增 `nestedArrays` / `mixedTypeField` / `referenceTotals` 等字段，`dependsOn` 在一个下游依赖两个前置时是数组。56 个场景 / 112 个端点 / 233 条自测。 |
