# ElevenLabs 注册服务

`elevenlabs-register` 是独立的 Playwright sidecar。默认一次注册一个账号，也可按顺序批量注册最多 20 个账号。它与共享运行配置卷配合，使用 YYDS 自有域名邮箱，并可选择 YesCaptcha 或 Captcha Gateway 完成 ElevenLabs 邮件注册流程。

## 运行方式

Compose 默认配置：

```text
容器地址：http://elevenlabs-register:8093
宿主机地址：http://127.0.0.1:18093
浏览器：容器内 Chromium，headless
网络：默认直连；代理为可选项
运行配置：/app/data/runtime-config.json
凭据文件：/app/data/elevenlabs-credentials.json
```

启动或重建：

```powershell
docker compose up -d --build elevenlabs-register
```

管理页入口为 <http://127.0.0.1:18000/elevenlabs> 的“注册机”和“运行配置”标签。

## 配置

在“运行配置”中填写：

- 打码供应商：`YesCaptcha` 或 `Captcha Gateway`
- 所选供应商的 API Key 和 Endpoint
- `YYDS API Key`
- YYDS 已拥有并验证的邮箱域名
- 可选代理 URL
- 注册超时

代理留空时使用直连，不会读取系统代理或其他容器端口。填写固定代理时支持 HTTP、HTTPS、SOCKS4、SOCKS4A、SOCKS5 和 SOCKS5H，日志只显示脱敏后的 scheme、host 和 port。

推荐在运行配置中填写 1024Proxy 白名单动态 IP API，例如：

```text
https://white.1024proxy.com/white/api?region=Rand&num=1&time=10&format=1&type=txt
```

注册机会在每个账号开始前按需提取 Sticky IP。会话时长应覆盖完整注册（建议不少于 10 分钟）。同一出口 24 小时内不会再开第二个免费账号；配置动态 IP 后最多 3 路并发，每路独占一个出口。直连或只有一个固定出口时自动降到 1 路，避免同一 IP 触发 ElevenLabs 的免费账号风控。

共享配置文件以 `0600` 权限原子写入 Docker 卷。读取接口仅返回密钥是否已配置，不返回密钥原文。

Captcha Gateway 默认地址为 `https://sub.aixiangshu.com`，注册机会按同步协议调用 `POST /captcha/solve`，通过 `Authorization: Bearer` 传 Key，并发送 hCaptcha 的页面 URL、siteKey、User-Agent、动态 `rqdata` 以及当前注册代理。若 Gateway 资源要求任务代理而运行配置仍为直连，服务会返回缺少代理的错误；此时需填写与浏览器会话一致的代理。配置预检不会创建付费任务，Gateway Key 会在第一次真实求解时校验。

## 接口

| 方法 | 路径 | 外部副作用 |
| --- | --- | --- |
| `GET` | `/healthz` | 无 |
| `GET` | `/v1/accounts` | 无，读取已注册账号列表 |
| `POST` | `/v1/preflight` | 仅连接 ElevenLabs 注册页 |
| `POST` | `/v1/dry-run` | 打开注册页并填写无效占位值，不提交 |
| `POST` | `/v1/register` | 创建邮箱、调用所选打码供应商并提交真实注册；请求体可带 `{"count": N}`，按顺序注册 1 到 20 个账号 |
| `POST` | `/v1/accounts/{id}/refresh` | 使用该账号捕获的 API Key 刷新套餐与额度 |

三个注册动作都有对应的 `/stream` 路径，以 Server-Sent Events 连续返回 `log`、`complete` 和 `error` 事件。管理页的实时日志窗口直接消费这些事件。

生产访问应设置 `ELEVENLABS_REGISTER_KEY`。主服务会使用同一个 Key 调用 sidecar。

## 注册流程

1. 验证直连或指定代理能访问 ElevenLabs。
2. 使用 YYDS 创建一个自有域名邮箱并生成随机密码。
3. 启动一次性 Chromium profile，加载注册页。
4. 填写邮箱、密码并勾选条款，调用所选供应商解 hCaptcha。
5. 将 token 预置到页面桥接层，由 ElevenLabs 表单自己的 `execute({ async: true })` 读取后提交；日志同时显示 Stripe 风控等待和 `/v1/user/pre-sign-up` HTTP 状态。
6. 等待页面进入邮件验证状态。
7. 轮询邮箱并提取受 host allowlist 限制的 HTTPS 验证链接。
8. 在同一浏览器上下文打开验证链接。
9. 使用同一邮箱密码登录，确认进入 `/app/home` 或 onboarding。
10. 捕获 `/v1/user` 或 `/v1/user/subscription` 响应中的套餐、已用额度、额度上限和重置时间。
11. 按邮箱更新保存账号；管理页列表显示邮箱、密码、套餐、状态和额度。
12. 批量注册时按顺序重复以上流程，账号之间暂停数秒；某个账号失败后继续下一个，并在实时日志中标记进度。

每次运行结束后临时浏览器 profile 会删除。验证链接日志会删除查询字符串，密码、Cookie、Firebase Token 和邮箱 API Token 不进入日志。

账号文件只保存在 `elevenlabs-media` Docker 卷中并以 `0600` 权限写入。账号 API Key 不会由列表接口返回；只有后端额度刷新逻辑可以读取。若登录页面没有返回账号 API Key，首次捕获的额度仍会显示，但刷新按钮会返回“额度不可刷新”，而不是误用网关全局 Key。

## 页面兼容

定位优先使用 ElevenLabs 的 `data-testid`，同时为邮箱、密码和提交按钮提供 `name`、`type` 与精确按钮文本回退。hCaptcha 桥接层会捕获真实 widget ID，并保持官方异步执行返回值 `{ response, key }`；它不会在点击前主动调用 React `onVerify`，从而避免重复注册流程。首次页面只加载出空壳时会自动 reload 一次，仍未出现表单才终止。

## 验证

无副作用验证：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18093/v1/preflight -ContentType application/json -Body '{}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18093/v1/dry-run -ContentType application/json -Body '{}'
```

测试：

```powershell
docker run --rm -v "${PWD}/services/auto_register:/app" -w /app elevenlabs-register-local:latest python -m unittest discover -s . -p "test_elevenlabs_*.py"
```

管理页可填写 1 到 20 的注册数量。CLI 也可批量：

```powershell
python -m elevenlabs_assisted --config elevenlabs.local.json run --count 5
```

真实注册尚未由自动化验收主动执行，因为它会创建外部账号并消耗验证码额度。应从管理页确认弹窗后执行一次，用于最终验证邮件模板、验证跳转和首次 onboarding。
