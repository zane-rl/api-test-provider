# 研发环境运行API演示服务

包内只有Python标准库项目，最低Python 3.6，不需要pip依赖、Mongo或Redis。选一台研发Linux服务器，使用可被HDP后端和演示电脑访问的IP:端口。

2026-10-26收到本次地址`http://43.136.128.102:18090`，目标为CentOS 7 / Python 3.6.8。旧包因直接导入Python 3.7才提供的`ThreadingHTTPServer`而启动失败。修订版使用`HTTPServer + ThreadingMixIn`，并将打包工具的子进程参数改为3.6可用形式，无需升级系统Python。实际远端启动及连通性仍须确认，不把本地回归记为Linux部署通过。

## Linux直接运行

解压并进入目录，前台启动：

```bash
tar -xzf api-test-provider-demo-20261026.tar.gz
cd api-test-provider-demo-20261026
python3 server.py --host 0.0.0.0 --port 18090 --advertise-base-url http://10.0.0.8:18090 --quiet
```

将`10.0.0.8:18090`换成真实研发地址。`--host`只控制监听；`--advertise-base-url`控制文档servers、令牌接口、场景清单与同源绝对分页地址。两者不可混用。Docker端口映射不同时，宣告外部端口。

前台确认正常后，可按研发环境已有进程管理方式托管；也可在独立演示目录用：

```bash
nohup python3 -u server.py --host 0.0.0.0 --port 18090 --advertise-base-url http://10.0.0.8:18090 --quiet > provider.log 2>&1 &
```

实例使用内存数据，重启会丢失该实例令牌、故障状态和请求日志，固定业务数据不变。使用独立演示实例，不替换共用18090；控制接口只对研发演示人员开放，避免其他人改动开关或看到请求日志。

## Docker运行

在有Docker的构建机进入包目录：

```bash
docker build -t api-test-provider:demo-20261026 .
docker run -d --name api-provider-demo-1026 -p 19090:18090 --restart unless-stopped api-test-provider:demo-20261026 --host 0.0.0.0 --port 18090 --advertise-base-url http://10.0.0.8:19090 --quiet
```

需要离线搬运镜像时：

```bash
docker save -o api-test-provider-demo-20261026-image.tar api-test-provider:demo-20261026
docker load -i api-test-provider-demo-20261026-image.tar
```

基础镜像默认`python:3.12-slim`，可通过`--build-arg PYTHON_BASE=<研发已有的Python镜像>`替换。容器只运行Provider，不包含HDP前后端、MDAgentService或模型配置。本轮本机无Docker命令，已提供构建文件，未构建或验证镜像。

## 上线前检查及素材生成

从HDP后端所在网络执行只读检查：

```bash
python3 tools/check_demo.py --base-url http://10.0.0.8:18090
```

从Provider本机检查时，可分别传监听地址和对外地址：

```bash
python3 tools/check_demo.py --base-url http://127.0.0.1:18090 --public-base-url http://10.0.0.8:18090
```

检查只读健康、场景清单及演示文档，不取Token、不调用业务、模型、HDP或故障开关。部分OpenAPI文档读取会进入Provider请求日志，这是正常观察记录。

用实际IP重新生成浏览器上传文件；已有目录不覆盖：

```bash
python3 tools/prepare_demo.py --base-url http://10.0.0.8:18090 --output demo-materials-linux-1026
```

容器外部端口为19090时，上述地址也使用19090。打开生成目录的README，按幕选择JSON。默认包中的`demo-materials`使用`provider.example.invalid`，仅为可核对的地址模板，不能直接上传演示。包内`openapi/`是原有自测静态基线，固定本机地址；演示只使用新生成的IP版素材目录。

## 现场检查顺序

1. 后端所在服务器能访问`/health`，电脑也能访问文档；不能只在Provider本机成功。
2. 场景清单baseUrl、上传文档servers、真实监听/映射端口一致，不残留localhost。
3. 研发前端与后端、Agent版本包含已交付API功能；研发前端使用研发Agent地址，不沿用本机8408。
4. 新建带日期的专用演示草稿，至少排练鉴权、依赖、拆表、共享计划和助手各一遍。实际UI结果另记，Provider自测不替代三方联调。
5. 按当前授权停在创建前；计划全部关闭。正式创建/保存/同步、旧配置迁移不包含在本次准备内。

故障开关、令牌及计数均属于该Provider实例。只对独立演示实例按场景操作；不调用无场景名的全局reset，不清理现有草稿或正式源。
