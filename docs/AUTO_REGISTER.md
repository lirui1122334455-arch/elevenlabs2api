# 协议自动补号（注册机）

> **原项目**：本能力构建在 [chenyme/grok2api](https://github.com/chenyme/grok2api) 之上。  
> **原作者**：[Chenyme](https://github.com/chenyme) · MIT License  
> 网关、账号池、管理后台等主体功能请参阅上游 README；本文只说明 **自动注册 / 补号** 部分。

---

## 1. 它是做什么的

当 **可用 Grok Build 账号数** 低于 **目标可用 Build** 时，网关会：

1. 从统一 **出口代理池**（Grok Web 节点）随机选一个 IP（或直连 / 应急代理）  
2. 调用 Python **sidecar**（默认 `:8091`）走 `accounts.x.ai` 协议注册  
3. 用临时邮箱收验证码，可选 ez-captcha 过 Turnstile  
4. 抽出 SSO → 导入 Web → **转 Build** → **chat 验活**（默认模型 `grok-4.5`；401/403 丢弃）  
5. 验活通过才计入可用 Build，补到目标为止  

注册生成的邮箱密码会使用账号凭据密钥加密保存。管理端 Web 账号池只显示“已保存”状态，管理员主动点击查看时才解密返回，便于 SSO 过期后尝试邮箱密码恢复。

| 模式 | 行为 |
| :-- | :-- |
| **立即补号到目标** | 不必开「启用自动补号」；立刻按 `need = 目标 − 当前可用 Build` 开批，补到目标 |
| **启用自动补号** | 按检查间隔轮询；仅当 `可用 Build < 目标` 时补到目标 |

「注册并发」= 并行注册槽位数，**不是**一次性追平全部缺口。为限制邮箱、验证码或上游系统故障的损耗，每轮尝试数不超过当前并发数，且最多 5 个；下一检查周期仍有缺口时再继续。

**适用场景**：自用号池保活、批量补 Web SSO（需自备干净出口与可用邮箱域）。  
**不适用**：绕过 xAI 服务条款的商业滥用；公共临时域在 xAI 侧常被拒信。

---

## 2. 架构

```
管理端 UI（设置 → 自动补号）
        │  配置 / 立即补号 / 停止 / 状态轮询
        ▼
  Go 网关调度器（backend/internal/application/autoregister）
        │  随机选 Grok Web 出口 IP
        ▼
 Python sidecar :8091（services/auto_register）
        │  建邮箱 → 访问 xAI → 收码 → 打码 → 注册 → 抽 SSO
        ▼
  导入 Grok Web SSO（可选 Console）
```

| 组件 | 路径 / 地址 | 职责 |
| :-- | :-- | :-- |
| 调度器 | `backend/internal/application/autoregister` | 阈值检查、并发、选代理、调 sidecar、导入账号 |
| HTTP API | `/api/admin/v1/auto-register/*` | `status` / `run-once` / `stop` |
| 设置 | 运行设置 `autoRegister.*` | 邮箱、打码、超时、阈值等（密钥加密入库） |
| Sidecar | `services/auto_register` · `POST /v1/register` | 协议注册实现 |
| 出口 | 管理端「出口代理」· scope `grok_web` | **唯一**代理配置入口 |

代理**不要**再维护 sidecar 的 `proxies.txt`（示例文件仅作参考，运行时不会自动读）。

---

## 3. 快速启用

### 3.1 启动 sidecar

**Docker Compose**（本仓库 `docker-compose.yml` 已包含）：

```bash
docker compose up -d auto-register
# Sidecar 地址填：http://auto-register:8091
```

**本地**：

```bash
cd services/auto_register
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python server.py
# 默认 http://127.0.0.1:8091  ·  GET /healthz
```

环境变量：`AUTO_REGISTER_HOST`（默认 `0.0.0.0`）、`AUTO_REGISTER_PORT`（默认 `8091`）。

### 3.2 配置出口（随机 IP）

1. 管理端 **运行设置 → 出口代理**  
2. 添加节点，作用域选 **Grok Web**  
3. 填 HTTP / HTTPS / SOCKS 代理（可带账号密码）  
4. 补号时从**可用且未冷却**的节点中加密随机选一个  

| 情况 | 行为 |
| :-- | :-- |
| 有健康 Grok Web 节点 | 随机使用该节点 |
| 无节点，填了「应急备用代理」 | 使用 `fallbackProxyURL` |
| 都为空 | **直连**（美国 VPS 等出口干净时可这样） |

部分住宅 SOCKS 会校验**你的源 IP 白名单**。若出现 `access forbidden` 或 TLS `WRONG_VERSION_NUMBER`，先到供应商后台加白本机/服务器公网 IP。

### 3.3 配置自动补号

打开 **运行设置 → 自动补号**：

| 配置项 | 建议 |
| :-- | :-- |
| 启用自动补号 | 定时：可用 Build < 目标时自动补到目标 |
| 目标可用 Build | 例如 100；水位按 **可调度 Build** 计 |
| 最低可用 Build | 可选标签，应 ≤ 目标；**不**作为停补条件 |
| 注册后 Build 验活 | 必须开启；关闭时自动补号配置不会生效 |
| 探测模型 | 默认 `grok-4.5`（chat 验活；仅 /models 正常不够） |
| 注册并发 | 1–5，只控制并行，建议先 1 |
| Sidecar 地址 | 本机 `http://127.0.0.1:8091`；Compose `http://auto-register:8091` |
| 邮箱服务商 | Cloud Temp Mail 或 YYDS |
| 临时邮箱 API / Key | 见下文「邮箱」 |
| 邮箱域名 | YYDS **必须**填自托管域；Cloudflare 可自动读域 |
| 打码 Key | ez-captcha 等；干净 IP 可试「跳过打码」 |
| 收信超时 / 单号超时 | 收信建议 ≥ 2m；单号默认约 8m |

保存后点 **立即补号一次** 验证。成功后「最近邮箱 / 最近出口」有值，Web 号池可查看已加密保存的邮箱密码，Build 号池新增一个验活通过的账号。

---

## 4. 邮箱配置

### 4.1 Cloud Temp Mail

兼容 [cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email) 一类自建实例（如自托管 Worker API）。

| 项 | 说明 |
| :-- | :-- |
| API Base | 如 `https://api-mail.example.com` |
| Auth 模式 | 常见 `x-admin-auth`（也可 `x-api-key` / Bearer 等） |
| Admin Key | 实例管理员密钥 |
| 创建/收信路径 | 默认 `/admin/new_address`、`/api/mails` |
| **自动读取域名** | 默认开：从 API 拉域名并与手动列表合并 |
| **随机子域/前缀** | 默认开：`enablePrefix` |
| **域名策略** | `rotate`（顺序+失败回退）/ `random` / `first` |

手动域名可留空（自动读取成功时）；关闭自动读取则必须填写域名。创建失败会按策略换域重试。

### 4.2 YYDS Mail

文档：https://vip.215.im/docs · API 默认 `https://maliapi.215.im/v1`

| 项 | 说明 |
| :-- | :-- |
| API Base | 可留空用默认 |
| API Key | 从供应商控制台获取（请求头 `X-API-Key`） |
| JWT | 可选，与 Key 二选一 |
| **邮箱域名** | **填你在 YYDS 托管并验证的自有域名** |

**重要**：YYDS **公共共享域名**经常被 xAI 拉黑，表现为「建号成功、等验证码超时」。  
本实现默认 **不允许** 公共域；仅调试时可勾选「允许 YYDS 公共域名（不推荐）」。

多个自有域用逗号分隔；失败按域名策略轮询。未填域名时会尝试 `prefer_owned`（账号下已验证的自有域）。

---

## 5. 打码（Turnstile）

- 配置 **ez-captcha**（或兼容 endpoint）+ Key 时，会解 Turnstile 再提交注册。  
- **跳过打码**：空 token 提交；仅干净住宅 / 优质出口有时能过。  
- 若错误含 `turnstile` / `captcha` / `challenge`，请关闭跳过并配置打码 Key。

---

## 6. 进度与状态

管理端状态卡片 + `GET /api/admin/v1/auto-register/status`：

| 字段 | 含义 |
| :-- | :-- |
| `running` / `stopping` / `inFlight` | 是否在跑、是否在停、进行中任务数 |
| `phase` | 当前阶段（见下表） |
| `progress` | 一行可读摘要 |
| `recentLogs` | 最近 sidecar 日志（含 `[phase:…]`） |
| `lastEmail` / `lastProxy` | 最近邮箱与出口节点名 |
| `successCount` / `failureCount` | 累计成功 / 失败 |
| `lastError` | 最近错误 |

运行中前端约 **1.5s** 刷新；空闲约 5s。

### 常见 phase

| phase | 含义 |
| :-- | :-- |
| `batch_start` | 批次开始（日志含 `available/min/target/force`，便于核对运行时配置是否与 UI 一致） |
| `pick_proxy` | 选择出口 |
| `call_sidecar` / `registering` | 调用注册服务 |
| `resolve_domains` | 解析邮箱域名 |
| `create_mailbox` | 创建临时邮箱 |
| `visit_home` / `load_signup` | 打开 xAI 注册页 |
| `send_email_code` / `wait_email_code` / `got_email_code` | 发码 / 等信 / 收到码 |
| `solve_turnstile` | 打码 |
| `create_account` | 提交注册 |
| `extract_sso` | 提取 SSO |
| `import_web` | 导入号池 |
| `convert_build` | Web SSO → Build OAuth（验活前置） |
| `probe_settle` | 入库后等待（默认 30s，对齐 grokcli-2api） |
| `probe_build` | Build **真实 chat ping**（默认模型 `grok-4.5`，非 `/models`）；401/403 删除 Web/Build，429/5xx/网络错误禁用 Build 并保留 Web/密码 |
| `done` | 验活通过才算成功 |
| `failed` / `stopped` | 失败 / 用户停止 |

### 管理 API（需管理员登录）

| 方法 | 路径 | 说明 |
| :-- | :-- | :-- |
| `GET` | `/api/admin/v1/auto-register/status` | 状态 |
| `POST` | `/api/admin/v1/auto-register/run-once` | 立即补号一批 |
| `POST` | `/api/admin/v1/auto-register/stop` | 停止当前批次 |

账号恢复密码同样只允许管理员访问：`GET /api/admin/v1/accounts/:id/recovery-password`。账号列表不会返回明文密码。

Sidecar：

| 方法 | 路径 | 说明 |
| :-- | :-- | :-- |
| `GET` | `/healthz` | 健康检查 |
| `POST` | `/v1/register` | 注册一号（由网关调用，一般不要手调） |

---

## 7. 注册流水线（sidecar 内部）

```
create_mailbox
    → visit_home / load_signup
    → validate_password
    → create_email_validation_code
    → wait_for_code（邮箱轮询）
    → verify_email_validation_code
    → solve Turnstile（可选）
    → create_account
    → fetch_sso_token（失败可 password + turnstile 恢复）
    → 返回 email / password / sso
```

日志行格式：`[phase:<name>] ...`，网关会写入 `recentLogs`。

---

## 8. 排障

| 现象 | 可能原因 | 处理 |
| :-- | :-- | :-- |
| 只有 `batch_start` → `batch_done`，无 `pick_proxy` | 运行时 `availableBuild ≥ target`（已达标）或 target 未保存成功 | 看状态行 **可用 Build / 目标**；保存后刷新；`need` 应为 `target − availableBuild` |
| 立即补号只补了 1 个 | **旧逻辑**把 need 当成注册并发 | 升级：need = 目标 − 可用 Build；并发只是并行度 |
| `timeout waiting for … email code` | 域名被拒信 / 邮箱 API 慢 | YYDS 换自托管域；加长收信超时；查邮箱后台是否有信 |
| `Cloud Temp Mail create failed` | 域名无效或 Key 错 | 开自动读域；检查 API Base / Auth / 路径 |
| TLS / `WRONG_VERSION_NUMBER` | 代理坏或源 IP 未加白 | 换节点；供应商加白；确认不是直连污染 DNS |
| Turnstile / captcha 拒绝 | 出口质量差或未打码 | 配 ez-captcha；换住宅节点 |
| `registration completed without SSO` | 注册过了但抽 cookie 失败 | 看日志 `extract_sso`；重试；检查代理稳定性 |
| `probe_build` / `build probe rejected … 403` | Build **chat** 测活失败（死号 / chat endpoint denied） | **正常**：号已删除不进池。注意：仅 `/models` 正常不够，必须以 chat 为准 |
| `build probe soft-fail` | chat 遇到 429、5xx 或网络错误，无法确定账号真假 | Build 被禁用且不计入目标；Web SSO、邮箱和密码保留，待上游恢复后人工检查 |
| 能注册但调用 403，`/models` 却正常 | 旧逻辑只测目录接口 | 升级：验活改为 chat ping（对齐 grokcli-2api）；403 丢弃 |
| sidecar 连不上 | 地址错或未启动 | `curl http://127.0.0.1:8091/healthz`；Compose 用服务名 |
| 本地 curl 一直超时 | 系统 `http_proxy` 劫持了 127.0.0.1 | `curl --noproxy '*'` 或设 `NO_PROXY=*` |

---

## 9. 安全

- 邮箱 Admin Key、打码 Key、YYDS JWT **加密**写入运行设置库；API 只回传「已配置」  
- 注册成功后的 SSO 进入账号池加密存储  
- 自动生成的邮箱密码使用同一 AES-256-GCM 凭据密钥加密；列表接口不返回明文
- 出口日志只记录去除账号、密码和查询参数后的代理地址
- **不要**把 `config.yaml` 密钥、`proxies.txt` 真代理、SSO 导出、sidecar `sso_output/` 提交 Git  
- 生产请限制管理端暴露面，补号仅内网调用 sidecar  

---

## 10. 本 fork 相对上游的变更摘要

上游 [chenyme/grok2api](https://github.com/chenyme/grok2api) 提供网关与号池；本 fork 在此基础上增加：

- 协议自动补号调度器 + 管理端 UI + sidecar  
- 统一出口池随机 IP（去掉双轨 `proxies.txt`）  
- 补号停止按钮  
- YYDS 自托管域名 / 禁止默认公共域  
- Cloud Temp Mail 自动域名、轮询回退、随机前缀  
- `phase` / `progress` / `recentLogs` 进度跟踪  
- **注册后 Build 验活**（思路对齐 [HM2899/grokcli-2api](https://github.com/HM2899/grokcli-2api)：settle → probe → 401/403 踢出号池）  
- 自动生成邮箱密码的加密保留与管理员按需查看
- 探测软故障隔离与单轮最多 5 个注册尝试

再分发时请保留 MIT 许可证与原作者 **Chenyme** 署名，并链接上游仓库。

---

## 11. 公开仓库脱敏清单

开源或推送到公开 Git 前请确认：

- [ ] 未提交 `config.yaml`（仅 `config.example.yaml`）  
- [ ] 未提交 `.env`、真实 `proxies.txt`、`data/`、`sso_output/`  
- [ ] 文档 / UI 占位符为 `example.com`，无个人邮箱域名或真实 Key  
- [ ] 截图不含账号、SSO、代理密码  
- [ ] 已阅读 [SECURITY.md](../SECURITY.md)  

本地密钥与数据库只存在于本机 gitignore 路径，**不会**也不应进入公开历史。

---

## 12. 相关链接

- 上游项目：https://github.com/chenyme/grok2api  
- 原作者：https://github.com/chenyme  
- 安全说明：[SECURITY.md](../SECURITY.md)  
- YYDS Mail：https://vip.215.im/docs  
- 本仓库 Compose：`docker-compose.yml` 中 `auto-register` 服务  
- 协议实现：`services/auto_register/protocol_register.py`  
- 调度实现：`backend/internal/application/autoregister/service.go`  
