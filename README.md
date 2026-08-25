# ElevenLabs Console

这是一个本地 ElevenLabs 注册与生成网关控制台。当前部署只提供 ElevenLabs 页面，不启动旧的自动补号服务，也不在界面中暴露原项目的账号、模型、图库或仪表盘入口。

管理端基础框架改造自 [chenyme/grok2api](https://github.com/chenyme/grok2api)，原作者为 [Chenyme](https://github.com/chenyme)，许可证见 [LICENSE](./LICENSE)。本仓库仅复用其 Go 管理鉴权和 React 工程基础，ElevenLabs 注册及网关作为独立服务运行。

## 功能

- ElevenLabs 单账号注册：YYDS 自有域名邮箱、可选 YesCaptcha / Captcha Gateway、邮箱验证和登录确认。
- 注册实时日志：持续显示验证码、Stripe 风控、注册请求、邮箱轮询、验证及登录阶段。
- 注册账号列表：保存并显示邮箱、密码、套餐、积分额度和重置时间，支持单账号额度刷新。
- 无副作用检查：网络预检及浏览器干跑均不会创建邮箱或提交注册。
- 运行配置：ElevenLabs API Key、可选代理、超时、打码供应商及独立密钥、YYDS 和邮箱域名。
- 生成网关：Sound Effects，以及实验性的 GPT Image 2 图片通道。
- 密钥隔离：运行密钥写入本地 Docker 卷，接口只返回“是否已配置”。
- 默认直连：未配置代理时明确使用 `direct`，不继承系统代理。

## 架构

```text
Browser /elevenlabs
        |
        v
Go admin/auth adapter :18000
        |--------------------------|
        v                          v
ElevenLabs gateway :18092    Registration service :18093
        |                          |
        v                          +--> YYDS Mail
ElevenLabs API                    +--> YesCaptcha / Captcha Gateway
                                   +--> Playwright -> elevenlabs.io
```

`18092` 和 `18093` 只绑定到宿主机 `127.0.0.1`。浏览器通过带管理员鉴权的 `/api/admin/v1/elevenlabs/*` 接口访问两个 sidecar。

## 启动

仓库根目录执行：

```powershell
docker compose up -d --build --remove-orphans
```

打开 <http://127.0.0.1:18000/elevenlabs>，使用 `config.yaml` 中配置的管理员账号登录。

当前 Compose 服务：

| 服务 | 作用 | 宿主机地址 |
| --- | --- | --- |
| `console` | 管理鉴权与 ElevenLabs API 适配层 | `http://127.0.0.1:18000` |
| `elevenlabs-gateway` | 生成网关及共享运行配置 | `http://127.0.0.1:18092` |
| `elevenlabs-register` | 浏览器注册服务 | `http://127.0.0.1:18093` |

查看状态：

```powershell
docker compose ps
docker compose logs -f elevenlabs-register
```

## 首次配置

进入“运行配置”页：

1. ElevenLabs API Key 只用于声音和图片生成；没有 Key 时注册机仍可使用，生成按钮保持禁用。
2. 代理留空即直连。只有确实需要其他出口时才填写完整的 HTTP/HTTPS/SOCKS URL。
3. 选择 YesCaptcha 或 Captcha Gateway，填写所选供应商的 Key，再填写 YYDS Key 和已验证的自有邮箱域名。
4. 点击“验证配置”，分别检查 ElevenLabs 网络、API Key、当前打码供应商和 YYDS。
5. 在“注册机”页先运行“网络预检”和“浏览器预检”，再决定是否执行真实注册。

真实注册会创建邮箱、调用验证码服务并向 ElevenLabs 提交账号，界面会在执行前二次确认。成功后的邮箱、密码和额度快照保存在本地 `elevenlabs-media` Docker 卷中，不会写入 Git。账号列表只在管理员页面和受鉴权的管理接口中提供，捕获的账号 API Key 不会返回给浏览器。

## 当前状态

- 直连 ElevenLabs 已验证为 HTTP 200。
- YesCaptcha 和 YYDS 凭据已通过只读验证；Captcha Gateway 已接入并配置，Key 会在首次真实求解时校验。
- 浏览器干跑已验证注册页定位符及 hCaptcha widget 捕获且未提交表单。
- 拦截式无副作用诊断已验证 hCaptcha 的 React 异步执行结果能够触发 `/v1/user/pre-sign-up`，不会创建账号。
- 注册账号列表、密码显隐和额度展示已接通；真实注册成功后会自动出现记录。
- ElevenLabs API Key 尚未配置，因此生成网关在线但计费生成不可用。
- 尚未自动执行一次真实账号注册；该动作留给管理页中的明确确认。

## 测试

```powershell
cd frontend
pnpm lint
pnpm build
```

```powershell
docker run --rm -v "${PWD}/services/auto_register:/app" -w /app elevenlabs-register-local:latest python -m unittest discover -s . -p "test_elevenlabs_*.py"
docker run --rm -v "${PWD}/services/elevenlabs_gateway:/app" -w /app elevenlabs-gateway-local:latest python -m unittest discover -s . -p "test_*.py"
```

更多说明：

- [注册服务](./docs/ELEVENLABS_REGISTER.md)
- [生成网关](./docs/ELEVENLABS_GATEWAY.md)
- [实现状态与风险](./docs/ELEVENLABS_REGISTER_FEASIBILITY.md)

请遵守 ElevenLabs、邮箱服务和验证码服务的使用条款，不要用于批量滥用或规避平台限制。
