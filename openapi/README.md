# 按鉴权类型拆分的 OpenAPI 文档

> 由 `tools/export_openapi_by_auth.py` 生成，**勿手改**；改了场景后重跑该脚本。

本目录把测试 API 提供服务的 56 个场景 / 112 个端点按**鉴权类型**合并成几份 OpenAPI 3.0.3 文档，供其他项目一次读出某类鉴权下的全部接口，再逐个调用做测试。文档由场景注册表生成，与服务的实际行为一致（单场景文档见 `/s/<场景>/openapi.json`，另有 `.yaml` / `.yml`）。

每份文档都提供 `.yaml` / `.yml` / `.json` 三种扩展名，内容等价：三个文件由同一个文档对象序列化而来，不是分别维护的。全部文档都是 OpenAPI 3.0.3；**不提供** Swagger 2.0 与 OpenAPI 3.1 的正常文档，这两种版本只有「应被拒绝」的反例（见文末）。

## 文件一览

| 鉴权类型 | 说明 | 静态文件 | 在线地址 | 场景 | 端点（DATA / TOKEN / FAULT） |
|---|---|---|---|---|---|
| `none` 无鉴权 | 不需要任何凭证；分页、请求响应、依赖、故障、类型不一致类场景都在这里 | [`none.yaml`](none.yaml) · [`.yml`](none.yml) · [`.json`](none.json) | `http://127.0.0.1:18090/openapi/none.<yaml\|yml\|json>` | 29 | 60 / 0 / 10 |
| `bearer` Bearer Token | 固定令牌，或先调登录接口取令牌再注入 Header / Query | [`bearer.yaml`](bearer.yaml) · [`.yml`](bearer.yml) · [`.json`](bearer.json) | `http://127.0.0.1:18090/openapi/bearer.<yaml\|yml\|json>` | 10 | 11 / 7 / 0 |
| `apiKey` API Key | Header / Query / Cookie 三种位置，含前缀与附加鉴权参数 | [`apiKey.yaml`](apiKey.yaml) · [`.yml`](apiKey.yml) · [`.json`](apiKey.json) | `http://127.0.0.1:18090/openapi/apiKey.<yaml\|yml\|json>` | 7 | 7 / 0 / 0 |
| `basic` HTTP Basic | 用户名 + 密码，含附加鉴权参数 | [`basic.yaml`](basic.yaml) · [`.yml`](basic.yml) · [`.json`](basic.json) | `http://127.0.0.1:18090/openapi/basic.<yaml\|yml\|json>` | 2 | 2 / 0 / 0 |
| `oauth2` OAuth2 客户端凭证 | client_credentials；client_secret_post / basic、令牌按次数或按时间失效 | [`oauth2.yaml`](oauth2.yaml) · [`.yml`](oauth2.yml) · [`.json`](oauth2.json) | `http://127.0.0.1:18090/openapi/oauth2.<yaml\|yml\|json>` | 7 | 7 / 7 / 0 |
| `fixedParam` 固定参数 | Header + Query + Cookie 三处同时带固定值 | [`fixedParam.yaml`](fixedParam.yaml) · [`.yml`](fixedParam.yml) · [`.json`](fixedParam.json) | `http://127.0.0.1:18090/openapi/fixedParam.<yaml\|yml\|json>` | 1 | 1 / 0 / 0 |
| `all` 全部场景 | 全部鉴权类型 | [`all.yaml`](all.yaml) · [`.yml`](all.yml) · [`.json`](all.json) | `http://127.0.0.1:18090/openapi/all.<yaml\|yml\|json>` | 56 | 88 / 14 / 10 |

## 本地启动与获取文档

```bash
./run.sh                        # 前台启动，监听 http://127.0.0.1:18090（Ctrl+C 退出）
./run.sh --port 9001 --quiet    # 换端口 / 不打印每条请求
./stop.sh                       # 停掉 18090 上的实例；--port 9001 停指定端口

curl -s http://127.0.0.1:18090/health                                # {"status":"ok"}
curl -s http://127.0.0.1:18090/openapi/bearer.yaml                   # 某类鉴权的合并文档（YAML）
curl -s http://127.0.0.1:18090/openapi/bearer.yml                    # 同上，.yml 扩展名（内容与 .yaml 相同）
curl -s http://127.0.0.1:18090/openapi/bearer.json                   # 同一份文档的 JSON 版
curl -s http://127.0.0.1:18090/s/page-number/openapi.yaml            # 单场景文档，同样有 .yaml / .yml / .json
curl -s http://127.0.0.1:18090/__admin/scenarios | python3 -m json.tool   # 清单，openapiBundles 列出全部合并文档地址
```

- 服务只依赖 Python 3 标准库，不访问外网。
- 本目录的静态文件 `servers` 固定为 `http://127.0.0.1:18090`；**换端口启动时改用在线地址**，其 `servers` 跟随实际端口。
- 在线文档与静态文件由同一段代码生成，`tests/test_bundle.py` 会校验静态文件没有过期。

## 文档结构与解析约定

| 位置 | 含义 |
|---|---|
| `servers[0].url` | 服务根地址；路径统一带场景前缀 `/s/<场景>/...` |
| `tags[]` | 每个场景一个 tag，`name` = 场景名 |
| `tags[].x-auth` | 该场景的测试凭证：`credentials`（位置 / 名称 / 测试值 / 前缀）、`extraAuthParams`（附加鉴权参数）、`tokenFlow`（取令牌方式、令牌路径、注入位置） |
| `tags[].x-openapi-url` | 该场景的单场景文档地址 |
| `components.securitySchemes` | 按场景改名为 `<场景>.<原名>`（不同场景的同名 scheme 配置不同） |
| `components.parameters` | 场景的公共参数，同样改名为 `<场景>.<原名>`；引用处保留 `$ref`（不内联），可能出现在操作级 `paths.*.*.parameters`，也可能出现在路径项级 `paths.*.parameters` |
| `paths.*.*.security` | 该接口要用的 scheme；`[]` 表示不需要鉴权（登录 / 令牌接口） |
| `paths.*.*.operationId` | `<场景驼峰>_<原 operationId>`，全文唯一；原值在 `x-original-operation-id` |
| `paths.*.*.x-scenario` / `x-auth-type` | 所属场景 / 鉴权类型 |
| `paths.*.*.x-test.role` | `DATA` 业务数据接口（该调、该同步）/ `TOKEN` 登录与令牌接口（**不要当数据接口**）/ `FAULT` 故障注入 |
| `paths.*.*.x-test` 其余字段 | `paginationType` + `pagination`（分页参数名与位置）、`recordPath`、`primaryKey`、`expectedTotal`（期望总条数）、`requiresAuth`、`note`，与 `/__admin/scenarios` 一致 |

## 推荐测试流程

1. 启动服务，读取某份合并文档，按 `x-test.role == DATA` 过滤出要测的接口。
2. `POST http://127.0.0.1:18090/__admin/reset?scenario=<场景>` 复位该场景（清请求日志、作废令牌）。
3. 按 tag 的 `x-auth` 配置凭证；有 `tokenFlow` 的先调令牌接口取令牌，再按 `inject` 注入。
4. 按 `x-test.pagination` 翻页取完全部记录，从 `recordPath` 取记录，断言条数等于 `expectedTotal`。
5. `GET http://127.0.0.1:18090/__admin/requests?scenario=<场景>` 查看服务端实际收到的请求，断言鉴权位置、翻页参数等。

注意：`/__admin/config` 与 `faults` 场景的计数器是进程级的，改配置或跑 `faults` 的用例需独占实例或串行；`oauth2-paged-expiring` 每页服务端延迟 6 秒，一次完整同步约 30 秒。各端点的特殊语义见仓库根目录 README.md。

## 各鉴权类型明细

### `none` 无鉴权 — [`none.yaml`](none.yaml)

不需要任何凭证；分页、请求响应、依赖、故障、类型不一致类场景都在这里。

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **none** | 鉴权方式 NONE | `GET /s/none/items`<br>DATA | 无 | NONE | `data.items` | `id` | 20 |
| **root-array** | 记录路径 = 根数组；分页方式 NONE | `GET /s/root-array/warehouses`<br>DATA | 无 | NONE | 根（空串） | `id` | 4 |
| **page-number** | PAGE_NUMBER × QUERY；startFrom 1/0；不配每页数量靠空页结束 | `GET /s/page-number/products`<br>DATA | 无 | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `sku` | 57 |
|  |  | `GET /s/page-number/products0`<br>DATA |  | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 0` | `data.items` | `sku` | 57 |
|  |  | `GET /s/page-number/products-nosize`<br>DATA |  | PAGE_NUMBER<br>`page(QUERY)` `起始 1` `固定每页 10` | `data.items` | `sku` | 57 |
| **offset** | OFFSET × QUERY；totalPath 结束；记录无主键 | `GET /s/offset/movements`<br>DATA | 无 | OFFSET<br>`offset(QUERY)` `limit(QUERY)` `起始 0` | `data.items` | 无 | 103 |
| **cursor-query** | CURSOR × QUERY；next_cursor 为 null 结束 | `GET /s/cursor-query/events`<br>DATA | 无 | CURSOR<br>`cursor(QUERY)` `limit(QUERY)` | `data` | `event_id` | 73 |
| **cursor-header** | CURSOR × HEADER | `GET /s/cursor-header/events`<br>DATA | 无 | CURSOR<br>`X-Cursor(HEADER)` `limit(QUERY)` | `data` | `event_id` | 73 |
| **cursor-body** | CURSOR × BODY（E3-D1 修复后的能力） | `POST /s/cursor-body/events/search`<br>DATA | 无 | CURSOR<br>`cursor(BODY)` `limit(BODY)` | `data` | `event_id` | 73 |
| **next-url** | NEXT_URL：同源绝对 / 相对 / 跨主机三种 | `GET /s/next-url/suppliers`<br>DATA | 无 | NEXT_URL<br>`next / ABSOLUTE_SAME_ORIGIN` | `data` | `id` | 41 |
|  |  | `GET /s/next-url/suppliers-relative`<br>DATA |  | NEXT_URL<br>`next / RELATIVE` | `data` | `id` | 41 |
|  |  | `GET /s/next-url/suppliers-crosshost`<br>DATA |  | NEXT_URL<br>`next / ABSOLUTE_CROSS_ORIGIN` | `data` | `id` | 41 |
| **body-page** | PAGE_NUMBER × BODY + 请求体值类型保真 | `POST /s/body-page/products/query`<br>DATA | 无 | PAGE_NUMBER<br>`page(BODY)` `size(BODY)` `起始 1` | `data.items` | `sku` | 45 |
| **next-link-only** | 只能跟随响应里 next 完整地址翻页、无任何分页参数 → 应标记为不支持 | `GET /s/next-link-only/records`<br>DATA | 无 | NEXT_URL<br>`next / ABSOLUTE_SAME_ORIGIN` | `data` | `id` | 35 |
| **echo** | Query / 请求体编码与类型保真验证 | `GET /s/echo/echo`<br>DATA | 无 | NONE | `data.items` | `id` | 1 |
|  |  | `POST /s/echo/echo-body`<br>DATA |  | NONE | `data.items` | `id` | 1 |
| **single-object** | 记录路径 × 单对象 → 1 条记录 | `GET /s/single-object/profile`<br>DATA | 无 | NONE | `data` | `id` | 1 |
| **biz-code** | 成功条件 EQ/NE × 数值/字符串 × 多允许值 | `GET /s/biz-code/ok-zero`<br>DATA | 无 | NONE | `data.items` | `id` | 3 |
|  |  | `GET /s/biz-code/ok-200`<br>DATA |  | NONE | `data.items` | `id` | 3 |
|  |  | `GET /s/biz-code/ok-string`<br>DATA |  | NONE | `data.items` | `id` | 3 |
|  |  | `GET /s/biz-code/biz-fail`<br>DATA |  | NONE | `data.items` | `id` | 0 |
| **nested** | 对象字段展开 + 嵌套数组子表（子表复制父字段） | `GET /s/nested/orders`<br>DATA | 无 | NONE | `data.items` | `order_no` | 12 |
| **shared-params** | 公共参数识别：components.parameters + $ref（操作级 / 路径项级）；内联的同名或重复参数不应被当成公共参数 | `GET /s/shared-params/orders`<br>DATA | 无 | NONE | `data.items` | `id` | 3 |
|  |  | `GET /s/shared-params/customers`<br>DATA |  | NONE | `data.items` | `id` | 3 |
|  |  | `GET /s/shared-params/products`<br>DATA |  | NONE | `data.items` | `id` | 3 |
|  |  | `GET /s/shared-params/warehouses`<br>DATA |  | NONE | `data.items` | `id` | 3 |
| **root-object-record** | 记录路径 = 根对象 → 1 条记录；标量数组与对象数组保留为字段 | `GET /s/root-object-record/order`<br>DATA | 无 | NONE | 根（空串） | `orderNo` | 1 |
| **form-body-post** | 业务接口请求体为 application/x-www-form-urlencoded → 应标记为不支持 | `POST /s/form-body-post/orders/search`<br>DATA | 无 | NONE | `data.items` | `id` | 6 |
| **nested-two-level** | 记录里的对象数组的元素再带一层对象数组，两层都有可作主键的字段 | `GET /s/nested-two-level/orders`<br>DATA | 无 | NONE | `data.items` | `order_no` | 3 |
| **special-field-names** | 数组字段及其子字段的名字含连字符、中文、以数字开头 | `GET /s/special-field-names/products`<br>DATA | 无 | NONE | `data.items` | `id` | 3 |
| **depend-path** | 前置依赖映射 × PATH；逐父记录条数不同 | `GET /s/depend-path/products`<br>DATA | 无 | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `sku` | 30 |
|  |  | `GET /s/depend-path/products/{sku}/batches`<br>DATA |  | NONE | `data` | `batch_no` | 60 |
| **depend-body** | 前置依赖映射 × BODY | `GET /s/depend-body/customers`<br>DATA | 无 | NONE | `data.items` | `id` | 12 |
|  |  | `POST /s/depend-body/customer-orders/query`<br>DATA |  | NONE | `data.items` | `order_no` | 18 |
| **required-header-cookie** | 业务参数位置 HEADER / COOKIE 且必填、无默认值；可绑定到前置接口的字段 | `GET /s/required-header-cookie/tenants`<br>DATA | 无 | NONE | `data.items` | `id` | 3 |
|  |  | `GET /s/required-header-cookie/invoices`<br>DATA |  | NONE | `data.items` | `invoice_no` | 6 |
|  |  | `GET /s/required-header-cookie/quotas`<br>DATA |  | NONE | `data.items` | `quota_id` | 4 |
| **depend-empty-parent** | 依赖展开时前置接口成功但没有记录；下游直接带合法取值仍可正常调用 | `GET /s/depend-empty-parent/categories`<br>DATA | 无 | NONE | `data.items` | `id` | 0 |
|  |  | `GET /s/depend-empty-parent/categories/{categoryId}/products`<br>DATA |  | NONE | `data.items` | `product_id` | 0 |
| **depend-mixed-key** | 前置接口同一个取值字段有的记录是数字、有的是文本；只差类型的取值如何发起下游请求；一个下游的两个参数分别取自两个前置接口 | `GET /s/depend-mixed-key/shops`<br>DATA | 无 | NONE | `data.items` | `id` | 4 |
|  |  | `GET /s/depend-mixed-key/shops/{ref}/staff`<br>DATA |  | NONE | `data.items` | `staff_no` | 5 |
|  |  | `GET /s/depend-mixed-key/seasons`<br>DATA |  | NONE | `data.items` | `code` | 2 |
|  |  | `GET /s/depend-mixed-key/sales`<br>DATA |  | NONE | `data.items` | `sale_id` | 6 |
| **faults** | 错误处理策略、重试、限流、超时、非 JSON、截断、结构漂移、重定向 | `GET /s/faults/always-500`<br>FAULT | 无 | NONE | `data.items` | `id` | — |
|  |  | `GET /s/faults/flaky`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /s/faults/biz-error`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /s/faults/rate-limit`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /s/faults/slow`<br>FAULT |  | NONE | `data.items` | `id` | 5 |
|  |  | `GET /s/faults/not-json`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /s/faults/huge`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /s/faults/drifted`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /s/faults/redirect`<br>FAULT |  | NONE | `data.items` | `id` | — |
|  |  | `GET /s/faults/ok`<br>FAULT |  | NONE | `data.items` | `id` | 5 |
| **depend-switchable** | 分支依赖 A→B→C、A→D；按接口切换正常 / 持续失败，验证连续失败暂停与修复后恢复 | `GET /s/depend-switchable/customers`<br>DATA | 无 | NONE | `data.items` | `id` | 5 |
|  |  | `GET /s/depend-switchable/customers/{customerId}/orders`<br>DATA |  | NONE | `data.items` | `order_no` | 11 |
|  |  | `GET /s/depend-switchable/orders/{orderNo}/items`<br>DATA |  | NONE | `data.items` | `item_no` | 16 |
|  |  | `GET /s/depend-switchable/addresses`<br>DATA |  | NONE | `data.items` | `address_id` | 6 |
| **depend-two-roots** | 下游的两个参数分别取自两个互不依赖的根接口；三个接口各自可切换正常 / 持续失败 | `GET /s/depend-two-roots/warehouses`<br>DATA | 无 | NONE | `data.items` | `id` | 3 |
|  |  | `GET /s/depend-two-roots/periods`<br>DATA |  | NONE | `data.items` | `code` | 2 |
|  |  | `GET /s/depend-two-roots/stock`<br>DATA |  | NONE | `data.items` | `stock_id` | 12 |
| **page-fail-midway** | 页码分页取到第 3 页起失败；修复（切开关）后可完整取完 | `GET /s/page-fail-midway/records`<br>DATA | 无 | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `id` | 50 |
| **type-mismatch** | 验证接口时把真实响应与已保存字段类型做兼容性比对：文档声明与实际返回故意不一致（全项目唯一，含 2 个不该判不兼容的负例与 1 个对照组） | `GET /s/type-mismatch/number-as-string`<br>DATA | 无 | NONE | `data.items` | `id` | 6 |
|  |  | `GET /s/type-mismatch/string-as-number`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /s/type-mismatch/bool-as-string`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /s/type-mismatch/bool-as-string-consistent`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /s/type-mismatch/datetime-as-epoch`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /s/type-mismatch/object-as-string`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /s/type-mismatch/int-vs-decimal`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /s/type-mismatch/nullable-mostly-null`<br>DATA |  | NONE | `data.items` | `id` | 6 |
|  |  | `GET /s/type-mismatch/consistent`<br>DATA |  | NONE | `data.items` | `id` | 6 |

### `bearer` Bearer Token — [`bearer.yaml`](bearer.yaml)

固定令牌，或先调登录接口取令牌再注入 Header / Query。

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **bearer-static** | BEARER_TOKEN + tokenSource=STATIC | `GET /s/bearer-static/items`<br>DATA | HEADER `Authorization` = `Bearer test-token-abc` | NONE | `data.items` | `id` | 20 |
| **bearer-request** | BEARER_TOKEN + tokenSource=REQUEST，令牌注入 Header Authorization | `POST /s/bearer-request/login`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /login`，体 `{"username": "test", "password": "test-pass"}`，取 `data.accessToken` | NONE | — | 无 | — |
|  |  | `GET /s/bearer-request/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **bearer-request-header** | tokenSource=REQUEST，令牌注入自定义 Header、不带 Bearer 前缀 | `POST /s/bearer-request-header/login`<br>TOKEN | HEADER `X-Access-Token` = `<由令牌接口签发>`<br>令牌：`POST /login`，体 `{"username": "test", "password": "test-pass"}`，取 `data.accessToken` | NONE | — | 无 | — |
|  |  | `GET /s/bearer-request-header/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **bearer-request-query** | tokenSource=REQUEST，令牌注入 Query | `POST /s/bearer-request-query/login`<br>TOKEN | QUERY `access_token` = `<由令牌接口签发>`<br>令牌：`POST /login`，体 `{"username": "test", "password": "test-pass"}`，取 `data.accessToken` | NONE | — | 无 | — |
|  |  | `GET /s/bearer-request-query/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **bearer-extra** | BEARER_TOKEN 与附加鉴权参数（Header + Query）并存 | `GET /s/bearer-extra/items`<br>DATA | HEADER `Authorization` = `Bearer test-token-abc`<br>附加 HEADER `X-App-Key` = `extra-key-1`<br>附加 QUERY `sign` = `test-sign` | NONE | `data.items` | `id` | 20 |
| **forbidden** | 401 与 403 分类 | `GET /s/forbidden/secret-items`<br>DATA | HEADER `Authorization` = `Bearer test-token-abc` | NONE | `data.items` | `id` | 0 |
|  |  | `GET /s/forbidden/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **token-json-body** | 取 Token 接口只收 application/json；Token 路径 data.token.value 且带有效期字段 | `POST /s/token-json-body/auth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /auth/token`，体 `{"appKey": "test-client", "appSecret": "test-secret"}`，取 `data.token.value` | NONE | — | 无 | — |
|  |  | `GET /s/token-json-body/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **token-form-body** | 取 Token 接口只收 application/x-www-form-urlencoded；Token 在顶层 tokenValue | `POST /s/token-form-body/auth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /auth/token`，表单 `appKey=test-client&appSecret=test-secret`，取 `tokenValue` | NONE | — | 无 | — |
|  |  | `GET /s/token-form-body/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **token-typed-fields** | 取 Token 返回除令牌（文本）与有效期（整数）外还有布尔、小数字段；有效期路径的候选不应包含布尔字段 | `POST /s/token-typed-fields/login`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /login`，体 `{"username": "test", "password": "test-pass"}`，取 `data.accessToken` | NONE | — | 无 | — |
|  |  | `GET /s/token-typed-fields/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **token-slow** | 取 Token 接口校验通过后固定延迟若干秒再返回，验证取令牌期间的等待与超时提示 | `POST /s/token-slow/login`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /login`，体 `{"username": "test", "password": "test-pass"}`，取 `data.accessToken` | NONE | — | 无 | — |
|  |  | `GET /s/token-slow/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |

快速验证（`B=http://127.0.0.1:18090`）：

```bash
B=http://127.0.0.1:18090
# bearer-static
curl -s "$B/s/bearer-static/items" -H "Authorization: Bearer test-token-abc"
# bearer-request
TOKEN=$(curl -s -X POST "$B/s/bearer-request/login" -H 'Content-Type: application/json' -d '{"username": "test", "password": "test-pass"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["accessToken"])')
curl -s "$B/s/bearer-request/items" -H "Authorization: Bearer $TOKEN"
# bearer-request-header
TOKEN=$(curl -s -X POST "$B/s/bearer-request-header/login" -H 'Content-Type: application/json' -d '{"username": "test", "password": "test-pass"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["accessToken"])')
curl -s "$B/s/bearer-request-header/items" -H "X-Access-Token: $TOKEN"
# bearer-request-query
TOKEN=$(curl -s -X POST "$B/s/bearer-request-query/login" -H 'Content-Type: application/json' -d '{"username": "test", "password": "test-pass"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["accessToken"])')
curl -s "$B/s/bearer-request-query/items?access_token=$TOKEN"
# bearer-extra
curl -s "$B/s/bearer-extra/items?sign=test-sign" -H "Authorization: Bearer test-token-abc" -H "X-App-Key: extra-key-1"
# forbidden
curl -s "$B/s/forbidden/secret-items" -H "Authorization: Bearer test-token-abc"
# token-json-body
TOKEN=$(curl -s -X POST "$B/s/token-json-body/auth/token" -H 'Content-Type: application/json' -d '{"appKey": "test-client", "appSecret": "test-secret"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["token"]["value"])')
curl -s "$B/s/token-json-body/items" -H "Authorization: Bearer $TOKEN"
# token-form-body
TOKEN=$(curl -s -X POST "$B/s/token-form-body/auth/token" -d appKey=test-client -d appSecret=test-secret | python3 -c 'import sys,json;print(json.load(sys.stdin)["tokenValue"])')
curl -s "$B/s/token-form-body/items" -H "Authorization: Bearer $TOKEN"
# token-typed-fields
TOKEN=$(curl -s -X POST "$B/s/token-typed-fields/login" -H 'Content-Type: application/json' -d '{"username": "test", "password": "test-pass"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["accessToken"])')
curl -s "$B/s/token-typed-fields/items" -H "Authorization: Bearer $TOKEN"
# token-slow
TOKEN=$(curl -s -X POST "$B/s/token-slow/login" -H 'Content-Type: application/json' -d '{"username": "test", "password": "test-pass"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["accessToken"])')
curl -s "$B/s/token-slow/items" -H "Authorization: Bearer $TOKEN"
```

### `apiKey` API Key — [`apiKey.yaml`](apiKey.yaml)

Header / Query / Cookie 三种位置，含前缀与附加鉴权参数。

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **apikey-header** | API_KEY in=header | `GET /s/apikey-header/items`<br>DATA | HEADER `X-Api-Key` = `test-key-123` | NONE | `data.items` | `id` | 20 |
| **apikey-query** | API_KEY in=query | `GET /s/apikey-query/items`<br>DATA | QUERY `api_key` = `test-key-123` | NONE | `data.items` | `id` | 20 |
| **apikey-cookie** | API_KEY in=cookie | `GET /s/apikey-cookie/items`<br>DATA | COOKIE `api_key` = `test-key-123` | NONE | `data.items` | `id` | 20 |
| **apikey-header-prefix** | API_KEY 放 Authorization 头且带自定义前缀 Token | `GET /s/apikey-header-prefix/items`<br>DATA | HEADER `Authorization` = `Token test-key-123` | NONE | `data.items` | `id` | 20 |
| **apikey-extra** | API_KEY 主凭证与附加鉴权参数并存 | `GET /s/apikey-extra/items`<br>DATA | HEADER `X-API-Key` = `test-key-123`<br>附加 HEADER `X-App-Key` = `extra-key-1` | NONE | `data.items` | `id` | 20 |
| **required-business-param** | 验证连接前检查请求值是否齐全：路径参数与 required Query 无固定值 / 无依赖 / 无测试值时返回 NEEDS_TEST_VALUES 且不发请求 | `GET /s/required-business-param/customers/{customerId}`<br>DATA | HEADER `X-Api-Key` = `test-key-123` | NONE | `data.items` | `id` | 1 |
| **dotted-param** | Query 参数名含点号（filter.name / page.size）时按原名透传，不拆成嵌套对象 | `GET /s/dotted-param/items`<br>DATA | HEADER `X-Api-Key` = `test-key-123` | NONE | `data.items` | `id` | 3 |

快速验证（`B=http://127.0.0.1:18090`）：

```bash
B=http://127.0.0.1:18090
# apikey-header
curl -s "$B/s/apikey-header/items" -H "X-Api-Key: test-key-123"
# apikey-query
curl -s "$B/s/apikey-query/items?api_key=test-key-123"
# apikey-cookie
curl -s "$B/s/apikey-cookie/items" -b "api_key=test-key-123"
# apikey-header-prefix
curl -s "$B/s/apikey-header-prefix/items" -H "Authorization: Token test-key-123"
# apikey-extra
curl -s "$B/s/apikey-extra/items" -H "X-API-Key: test-key-123" -H "X-App-Key: extra-key-1"
# required-business-param
curl -s "$B/s/required-business-param/customers/{customerId}" -H "X-Api-Key: test-key-123"
# dotted-param
curl -s "$B/s/dotted-param/items" -H "X-Api-Key: test-key-123"
```

### `basic` HTTP Basic — [`basic.yaml`](basic.yaml)

用户名 + 密码，含附加鉴权参数。

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **basic** | BASIC_AUTH | `GET /s/basic/items`<br>DATA | HEADER `Authorization` = `Basic dGVzdDp0ZXN0LXBhc3M=` | NONE | `data.items` | `id` | 20 |
| **basic-extra** | BASIC_AUTH 与附加鉴权参数并存 | `GET /s/basic-extra/items`<br>DATA | HEADER `Authorization` = `Basic dGVzdDp0ZXN0LXBhc3M=`<br>附加 QUERY `tenant` = `t-001` | NONE | `data.items` | `id` | 20 |

快速验证（`B=http://127.0.0.1:18090`）：

```bash
B=http://127.0.0.1:18090
# basic
curl -s "$B/s/basic/items" -H "Authorization: Basic dGVzdDp0ZXN0LXBhc3M="
# basic-extra
curl -s "$B/s/basic-extra/items?tenant=t-001" -H "Authorization: Basic dGVzdDp0ZXN0LXBhc3M="
```

### `oauth2` OAuth2 客户端凭证 — [`oauth2.yaml`](oauth2.yaml)

client_credentials；client_secret_post / basic、令牌按次数或按时间失效。

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **oauth2-post** | OAUTH2 客户端凭证，凭证放表单体 | `POST /s/oauth2-post/oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /s/oauth2-post/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **oauth2-basic** | OAUTH2 client_secret_basic + 令牌缓存失效后 401 重取并重放 | `POST /s/oauth2-basic/oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_basic，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /s/oauth2-basic/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **oauth2-extra** | OAUTH2 与附加鉴权参数并存；令牌请求不带附加参数 | `POST /s/oauth2-extra/oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>附加 HEADER `X-App-Key` = `extra-key-1`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /s/oauth2-extra/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **oauth2-expiring** | OAUTH2 令牌缓存与过期刷新：距过期不足 30 秒应提前重取 | `POST /s/oauth2-expiring/oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /s/oauth2-expiring/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |
| **oauth2-paged-maxuses** | 一个批次内多次业务请求：复用令牌遇 401 → 重取 → 重放同页 | `POST /s/oauth2-paged-maxuses/oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /s/oauth2-paged-maxuses/items`<br>DATA |  | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `id` | 50 |
| **oauth2-paged-expiring** | 一个批次内令牌临近过期：后端应主动重取，不应出现 401 | `POST /s/oauth2-paged-expiring/oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /s/oauth2-paged-expiring/items`<br>DATA |  | PAGE_NUMBER<br>`page(QUERY)` `page_size(QUERY)` `起始 1` | `data.items` | `id` | 50 |
| **oauth2-scope-required** | OAUTH2 授权范围：令牌请求必须带 scope=read，否则 400 invalid_scope | `POST /s/oauth2-scope-required/oauth/token`<br>TOKEN | HEADER `Authorization` = `<由令牌接口签发>`<br>令牌：`POST /oauth/token`，client_secret_post，`test-client` / `test-secret` | NONE | — | 无 | — |
|  |  | `GET /s/oauth2-scope-required/items`<br>DATA |  | NONE | `data.items` | `id` | 20 |

快速验证（`B=http://127.0.0.1:18090`）：

```bash
B=http://127.0.0.1:18090
# oauth2-post
TOKEN=$(curl -s -X POST "$B/s/oauth2-post/oauth/token" -d grant_type=client_credentials -d client_id=test-client -d client_secret=test-secret -d scope=read | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s "$B/s/oauth2-post/items" -H "Authorization: Bearer $TOKEN"
# oauth2-basic
TOKEN=$(curl -s -X POST "$B/s/oauth2-basic/oauth/token" -d grant_type=client_credentials -u test-client:test-secret -d scope=read | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s "$B/s/oauth2-basic/items" -H "Authorization: Bearer $TOKEN"
# oauth2-extra
TOKEN=$(curl -s -X POST "$B/s/oauth2-extra/oauth/token" -d grant_type=client_credentials -d client_id=test-client -d client_secret=test-secret -d scope=read | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s "$B/s/oauth2-extra/items" -H "Authorization: Bearer $TOKEN" -H "X-App-Key: extra-key-1"
# oauth2-expiring
TOKEN=$(curl -s -X POST "$B/s/oauth2-expiring/oauth/token" -d grant_type=client_credentials -d client_id=test-client -d client_secret=test-secret -d scope=read | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s "$B/s/oauth2-expiring/items" -H "Authorization: Bearer $TOKEN"
# oauth2-paged-maxuses
TOKEN=$(curl -s -X POST "$B/s/oauth2-paged-maxuses/oauth/token" -d grant_type=client_credentials -d client_id=test-client -d client_secret=test-secret -d scope=read | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s "$B/s/oauth2-paged-maxuses/items" -H "Authorization: Bearer $TOKEN"
# oauth2-paged-expiring
TOKEN=$(curl -s -X POST "$B/s/oauth2-paged-expiring/oauth/token" -d grant_type=client_credentials -d client_id=test-client -d client_secret=test-secret -d scope=read | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s "$B/s/oauth2-paged-expiring/items" -H "Authorization: Bearer $TOKEN"
# oauth2-scope-required
TOKEN=$(curl -s -X POST "$B/s/oauth2-scope-required/oauth/token" -d grant_type=client_credentials -d client_id=test-client -d client_secret=test-secret -d scope=read | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s "$B/s/oauth2-scope-required/items" -H "Authorization: Bearer $TOKEN"
```

### `fixedParam` 固定参数 — [`fixedParam.yaml`](fixedParam.yaml)

Header + Query + Cookie 三处同时带固定值。

| 场景 | 覆盖的后端能力 | 端点 | 鉴权与测试凭证 | 分页方式 | 记录路径 | 主键 | 期望总数 |
|---|---|---|---|---|---|---|---|
| **fixed-param** | FIXED_PARAM：Header + Query + Cookie 同时必填 | `GET /s/fixed-param/items`<br>DATA | HEADER `X-App-Id` = `app-001`<br>QUERY `sign` = `test-sign`<br>COOKIE `tenant` = `t-001` | NONE | `data.items` | `id` | 20 |

快速验证（`B=http://127.0.0.1:18090`）：

```bash
B=http://127.0.0.1:18090
# fixed-param
curl -s "$B/s/fixed-param/items?sign=test-sign" -H "X-App-Id: app-001" -b "tenant=t-001"
```

## 反例文档（应被拒绝的文档版本）

产品口径只支持 OpenAPI 3.0.x。下面两份文档版本不在支持范围内，**期望后端拒绝导入**（清单里 `expectedBackendSupport = REJECTED`）。它们不是正常场景：不计入场景数、不进上面的合并文档，只在线提供（地址见 `/__admin/scenarios` 的 `negativeDocuments`）。

| 文档 | 版本 | 推荐地址 | 其它扩展名 | 描述的接口 |
|---|---|---|---|---|
| `swagger2` | Swagger 2.0 | `http://127.0.0.1:18090/__negative/swagger2.json` | `.yaml` · `.yml` | `GET /__negative/items`（恒定 3 条） |
| `openapi31` | OpenAPI 3.1.0 | `http://127.0.0.1:18090/__negative/openapi31.yaml` | `.json` · `.yml` | `GET /__negative/items`（恒定 3 条） |
