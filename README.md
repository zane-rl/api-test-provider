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

## 启动

```bash
./run.sh                 # 监听 http://127.0.0.1:18090
./run.sh --port 9001     # 换端口（文档里的 servers[0].url 会跟着变）
./run.sh --quiet         # 不打印每条请求
```

不依赖外网、不装任何第三方库，Python 3 即可。**用完请停掉**，不要常驻后台占端口。

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
  openapi.py                由注册表动态生成 OpenAPI 3.0 文档 + Swagger 2.0 转换器
  auth.py                   A 鉴权（19 个场景）
  pagination.py             B 分页（8 个场景）
  payload.py                C 请求与响应（4 个场景）
  depend.py                 D 依赖（2 个场景）
  faults.py                 E 故障（1 个场景）
  __init__.py               REGISTRY：场景注册表（有序），名字重复会直接报错
tools/dump_scenario_table.py  导出下面那张场景总表
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
- 自测兜底：遍历每份文档声明的每个 path+method 真的发一次请求，断言非 404/405；
  分页用例只读 `/__admin/scenarios` 的分页元数据来翻页，清单写错用例就挂。

## 场景总表

所有业务路径形如 `/s/<场景>/<端点>`；每个场景的文档在 `/s/<场景>/openapi.json`（**该端点本身不需要鉴权**）。
`apikey-header` 与 `page-number` 额外提供 `/s/<场景>/swagger2.json`（Swagger 2.0 写法，守后端的 v2 转换路径）。

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
| **apikey-header** | API_KEY in=header；另提供 Swagger 2.0 文档 | `GET /items`<br>DATA | HEADER `X-Api-Key` = `test-key-123` | NONE | `data.items` | `id` | 20 |
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
| **fixed-param** | FIXED_PARAM：Header + Query + Cookie 同时必填 | `GET /items`<br>DATA | HEADER `X-App-Id` = `app-001`<br>QUERY `sign` = `test-sign`<br>COOKIE `tenant` = `t-001` | NONE | `data.items` | `id` | 20 |
| **bearer-extra** | BEARER_TOKEN 与附加鉴权参数（Header + Query）并存 | `GET /items`<br>DATA | HEADER `Authorization` = `Bearer test-token-abc`<br>附加 HEADER `X-App-Key` = `extra-key-1`<br>附加 QUERY `sign` = `test-sign` | NONE | `data.items` | `id` | 20 |
| **forbidden** | 401 与 403 分类 | `GET /secret-items`<br>DATA | HEADER `Authorization` = `Bearer test-token-abc` | NONE | `data.items` | `id` | 0 |
|  |  | `GET /items`<br>DATA |  | NONE | `data.items` | `id` | 20 |

#### B 分页

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **root-array** | 记录路径 = 根数组；分页方式 NONE | `GET /warehouses`<br>DATA | 无 | NONE | 根数组（空串） | `id` | 4 |
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

#### D 依赖

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **depend-path** | 前置依赖映射 × PATH；逐父记录条数不同 | `GET /products`<br>DATA | 无 | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `sku` | 30 |
|  |  | `GET /products/{sku}/batches`<br>DATA |  | NONE | `data` | `batch_no` | 60 |
| **depend-body** | 前置依赖映射 × BODY | `GET /customers`<br>DATA | 无 | NONE | `data.items` | `id` | 12 |
|  |  | `POST /customer-orders/query`<br>DATA |  | NONE | `data.items` | `order_no` | 18 |

#### E 故障

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **faults** | 错误处理策略、重试、限流、超时、非 JSON、截断、结构漂移、重定向 | `GET /always-500`<br>FAULT | 无 | NONE | `data.items` | `id` | — |
|  |  | `GET /flaky`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /biz-error`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /rate-limit`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /slow`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /not-json`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /huge`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /drifted`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /redirect`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /ok`<br>FAULT |  | NONE | `data.items` | `id` | 5 |

<!-- SCENARIO-TABLE:END -->

### 几个需要注意的语义

| 端点 | 行为 |
|---|---|
| `page-number` `/products-nosize` | **带了 `page_size` 返回 400**（不是静默忽略），响应无 `total`，靠空页结束 |
| `body-page` `/products/query` | `page` / `size` 必须是 JSON 数值，写成字符串返回 400 |
| `cursor-body` `/events/search` | `limit` 必须是 JSON 数值 |
| `next-url` `/suppliers-crosshost` | `next` 指向 `http://localhost:<端口>`，与文档里的 `127.0.0.1` 不同源，**后端应拒绝跟随**；拒绝时只应取到首页 15 条（`expectedTotal` 给的是数据总量 41） |
| `oauth2-post` `/oauth/token` | 带了 `Authorization` 头返回 400（证明后端选对了 client_secret_post） |
| `oauth2-basic` `/oauth/token` | 表单体里出现 `client_secret` 返回 400；签发的令牌**按使用次数失效**，默认第 3 次业务请求返回 401 |
| `oauth2-expiring` `/oauth/token` | 令牌**按时间过期**：`expires_in` 返回实际有效秒数（默认 40），到点后业务请求 401。用于验证后端「距过期不足 30 秒提前重取」——拿到令牌后等约 15 秒再发请求，应看到令牌端点被请求 2 次且业务请求无 401 |
| `oauth2-extra` `/oauth/token` | 令牌请求带了附加鉴权参数 `X-App-Key` 返回 400（后端约定：令牌请求不带附加参数） |
| `apikey-header-prefix` | `Authorization` 的值必须是 `Token test-key-123`，无前缀或前缀不符 401，错误消息里点名「缺少前缀 Token」 |
| `forbidden` `/secret-items` | 凭证正确也恒定 403；无凭证仍是 401 |
| 所有场景 | `PUT` / `PATCH` / `DELETE` 一律 405 + `Allow: GET, POST` |

### 依赖类的条数公式

| 子接口 | 公式 | 取值 | 合计 |
|---|---|---|---|
| `depend-path` `GET /products/{sku}/batches` | `(序号 * 7) % 5`，序号取自 `DPD-000NN` | 0~4 条 | 30 个商品共 **60** 条 |
| `depend-body` `POST /customer-orders/query` | `(序号 * 3) % 4`，序号取自 `CUS-000NN` | 0~3 条 | 12 个客户共 **18** 条 |

前 5 个父记录的条数分别是 `2 / 4 / 1 / 3 / 0` 与 `3 / 2 / 1 / 0 / 3`——有 0 条的父记录，用来验证空结果不算失败。
`sku` 不存在返回 404；`customerId` 缺失返回 400，不存在则返回空列表。

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
| `GET /__admin/scenarios` | 全部场景清单：文档地址、鉴权方式与测试凭证（含 `prefix` / `extraAuthParams` / `tokenFlow`）、每个端点的 `method` / `path` / `role` / `paginationType` / 分页参数名与位置 / `recordPath` / `primaryKey` / `expectedTotal` / `sampleFirst3` |
| `GET /__admin/requests?scenario=<名字>&limit=<N>` | 服务端实际收到的请求日志，按时间顺序；含序号、方法、路径、解码后的 Query、`rawQuery`、全部请求头、Cookie、原始请求体、响应状态码。内存最多保留最近 5000 条 |
| `POST /__admin/reset` | 全量复位：清空请求日志，复位 `flaky` / `rate-limit` 计数器、已签发令牌与令牌序号。**不改 config** |
| `POST /__admin/reset?scenario=<名字>` | 只复位该场景：清掉它的请求日志（其它场景的保留，序号不归零）、作废它签发的令牌；故障计数器只在 `scenario=faults` 时复位。场景名不存在返回 404 |
| `GET/POST /__admin/config` | 读取 / 调整可变参数：`oauth2BasicTokenMaxUses`（默认 2，即第 3 次业务请求令牌失效）、`oauth2ExpiringTokenTtlSeconds`（默认 40，`oauth2-expiring` 的令牌有效秒数）。改了之后 `/__admin/scenarios` 的 `tokenFlow` 会立刻反映新值 |
| `GET /__admin/state` | 当前配置、故障计数器、已签发令牌快照（排查用） |

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

`REQUEST_TOKEN` 的 `tokenFlow` 另有 `requestBody`（登录体）、`tokenPath`（`data.accessToken`）、
`expiresInPath`（`data.expiresIn`）。

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
5. 跑 `./run_tests.sh`——通用用例会自动覆盖新场景（文档可达性、清单一致性、翻页到底）。
   若新增的是 A 类鉴权场景或 B 类数据端点，记得同步 `tests/test_auth.py` /
   `tests/test_pagination.py` 里的数量断言。
6. 重新生成场景总表：`python3 tools/dump_scenario_table.py`，把结果贴回本文件的
   `<!-- SCENARIO-TABLE -->` 区块。

## 已知局限

- 请求日志只在内存，进程退出即丢；最多 5000 条，超出丢最早的。
- **并发共享的现状**：请求日志已能按场景隔离清理（`POST /__admin/reset?scenario=<名字>`），
  不同场景的用例可以并发跑同一个实例而不互相清掉日志。但仍有两处是**进程级全局**：
  `/__admin/config` 的配置项（`oauth2BasicTokenMaxUses` / `oauth2ExpiringTokenTtlSeconds`）、
  以及故障场景那几个计数器（它们本就只属于 `faults`）。所以：
  **改配置或跑 `faults` 场景的用例必须独占实例或串行**，其余场景并发跑没问题。
- 令牌有两种失效方式：`oauth2-basic` 按**使用次数**（可复现），`oauth2-expiring` 按**时间**。
  其余场景的令牌不失效，`expires_in` 恒为 3600。
- 只支持 HTTP，没有 HTTPS / mTLS 场景。
- `suppliers-crosshost` 的「异源」靠 `localhost` 与 `127.0.0.1` 的主机名差异构造，二者解析到同一进程；
  如果后端按 IP 而不是按主机名判同源，这个场景不会触发拒绝。
- 故障类端点没有进 `expectedTotal` 体系（它们的返回随调用次数变化），需要逐条按 README 的表断言。
- `not-json` / `huge` 这类端点的响应体不是常规结构，文档里给的是显式 schema 与示意性 example。

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-20 | v1：33 个场景 / 59 个端点 / 84 条自测用例。含 Commander 追加的 4 个鉴权场景（`apikey-header-prefix`、`apikey-extra`、`basic-extra`、`oauth2-extra`）与 `role` / 结构化鉴权配置要求。 |
| 2026-09-20 | H2：`POST /__admin/reset` 支持 `?scenario=` 按场景复位；新增 `oauth2-expiring`（令牌按时间过期，`expires_in` 默认 40 秒，可由 `/__admin/config` 调）；`/__admin/scenarios` 的 `tokenFlow` 改为实时取配置值。34 个场景 / 61 个端点 / 93 条自测。 |
