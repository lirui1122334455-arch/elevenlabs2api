# ElevenLabs 生成网关

`elevenlabs-gateway` 负责共享运行配置、Sound Effects 转发和 GPT Image 2 文生图/图生图。默认监听宿主机 `127.0.0.1:18092`，管理页面通过 Go 管理鉴权层访问它。

## 配置

配置优先从共享运行卷读取，可在“运行配置”页面热更新：

- ElevenLabs API Key
- ElevenLabs API Base URL
- 可选代理 URL，留空为直连
- 请求、生成和注册超时
- YesCaptcha / Captcha Gateway 选择、独立密钥与 YYDS 注册配置

环境变量只作为首次创建运行配置时的默认值。真实密钥不应提交到 `.env.example` 或 Git。

没有 API Key 时 `/healthz` 仍返回在线，但 `configured=false`，声音和图片生成按钮保持禁用。网关会从共享的 `elevenlabs-credentials.json` 读取所有已创建 API Key，`account_pool_size` 返回去重后的账号池数量。注册服务不依赖 ElevenLabs API Key。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/healthz` | 服务、API Key 和代理状态 |
| `GET` | `/v1/runtime-config` | 脱敏运行配置 |
| `PUT` | `/v1/runtime-config` | 更新运行配置 |
| `POST` | `/v1/runtime-config/preflight` | 验证网络、API Key、当前打码供应商和 YYDS |
| `GET` | `/v1/models` | 本地支持的模型列表 |
| `POST` | `/v1/sound-generation` | Sound Effects 音频生成 |
| `POST` | `/v1/images/generations` | GPT Image 2 图片生成 |
| `GET` | `/media/{sha256}.{ext}` | 已下载媒体 |

可用 `ELEVENLABS_GATEWAY_KEY` 保护 sidecar；主服务需配置相同 Key。

## 上游能力

Sound Effects 使用公开接口：

```text
POST https://api.us.elevenlabs.io/v1/sound-generation
xi-api-key: <scoped-api-key>
```

控制台保留 `eleven_text_to_sound_v2` 和 `eleven_text_to_sound_v3` 两个选择并按原模型名透传。当前 ElevenLabs 公开接口文档只列出 V2，V3 是否可用以账号所在区域的上游响应为准；网关不会把 V3 静默映射为 V2。

生成请求优先使用当前运行 Key。只有初始 POST 明确返回账号级拒绝（401、402、403、429 或 hCaptcha 要求）且尚未创建生成任务时，网关才依次切换账号池；任务已创建后的轮询、媒体下载、超时和 5xx 均不换号，避免重复生成或扣费。备用账号成功后会自动成为当前运行 Key。全部账号被拒绝时返回 `account_pool_exhausted`，错误中汇总尝试数量和上游错误码。

GPT Image 2 使用 ElevenLabs 正式 Flows 接口 `POST /v1/flows/image`，支持比例、分辨率、质量以及 `inline_base64` 参考图。创建后按不低于两秒的间隔轮询 `GET /v1/flows/image/{id}`，生成结果按 SHA-256 保存到本地媒体卷。图片能力要求 Pro 或更高套餐；免费账号会返回上游的套餐限制错误。账号切换不会绕过套餐要求，如果池内都是免费账号，网关会尝试完后明确返回账号池已耗尽。

## 启动与检查

```powershell
docker compose up -d --build elevenlabs-gateway
Invoke-RestMethod http://127.0.0.1:18092/healthz
Invoke-RestMethod http://127.0.0.1:18092/v1/models
```

完整控制台：<http://127.0.0.1:18000/elevenlabs>

运行配置预检分别返回：

- `gateway_ready`：网络正常且 ElevenLabs API Key 有效。
- `registration_ready`：网络、当前打码供应商、YYDS 和邮箱域名有效。Captcha Gateway 没有无扣费验 Key 接口，因此预检只确认已配置，Key 在首次真实求解时校验。
- `ready`：两者都已就绪。

账号列表中的“接入网关并刷新额度”会在需要时登录该账号、创建最小权限 API Key，并把它设为当前网关 Key。`gateway_ready=true` 只表示 Key 鉴权有效；账号风控或套餐限制仍会由具体生成接口返回明确错误。

## 安全边界

- sidecar 端口只绑定 `127.0.0.1`。
- 运行密钥不由 GET 接口返回。
- API 请求不继承系统代理；只使用显式的可选代理配置。
- 默认生成并发为 1。
- 响应体和媒体文件有大小上限。
- 上游 hCaptcha 要求会返回错误，不会在计费生成路径中自动调用打码平台。
