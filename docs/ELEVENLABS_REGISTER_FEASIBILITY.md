# ElevenLabs 注册实现状态与风险

验证日期：2026-08-25。

## 当前结论

ElevenLabs 注册不能通过替换其他站点的注册 URL 实现。当前方案采用独立 Playwright 服务，并只复用通用的邮箱接收能力。注册服务与生成网关共享一份专用运行配置，不依赖旧项目的账号池、模型路由或自动补号调度。

已验证：

- 直连 `https://elevenlabs.io/app/sign-up` 返回 HTTP 200。
- 注册页邮箱、密码和提交按钮可被浏览器定位。
- 无提交的浏览器干跑成功。
- YesCaptcha Key 已验证；Captcha Gateway 已接入并配置，未主动创建付费任务。
- YYDS Key 可读取域名，自有域名 `318ai.top` 和 `88.mivioo.xyz` 已验证。
- 服务重建后运行配置仍由本地 Docker 卷保留。
- 临时 Chromium profile 会在任务结束后清理。

尚未主动执行真实注册，因为该操作会创建外部邮箱、消耗验证码额度并提交 ElevenLabs 账号。

## 技术边界

ElevenLabs 当前注册流程涉及：

- Next.js 客户端表单
- Firebase Authentication
- ElevenLabs 预注册接口
- hCaptcha
- 邮件验证长链接
- 首次登录或 onboarding

浏览器流程只保存最终邮箱和密码，不导出 Cookie、localStorage 或 Firebase Token。验证链接仅允许 HTTPS 和配置的 ElevenLabs/Firebase host，日志删除全部查询参数。

## 状态机

```text
network_preflight
  -> create_mailbox
  -> launch_clean_profile
  -> load_signup
  -> fill_signup
  -> solve_hcaptcha
  -> submit_signup
  -> wait_verification_email
  -> open_verified_link
  -> load_signin
  -> authenticate
  -> save_credentials
```

## 剩余现场风险

- ElevenLabs 可随发布更改 DOM、预注册参数或验证码行为。
- 第一封真实验证邮件的模板和完整重定向链仍需一次真实注册确认。
- 新账号可能进入不同 onboarding 分支。
- 平台可能基于出口、域名或频率拒绝注册。
- 任一打码供应商返回有效任务结果，都不代表 ElevenLabs 一定接受该次挑战。

代码通过 `data-testid` 加语义选择器回退、一次页面 reload、错误分类和严格超时来降低变更风险，但不能保证第三方页面永久兼容。

## 最终验收建议

由管理员在控制台点击“注册一个账号”，确认弹窗后执行一次真实流程。验收标准：

1. YYDS 成功创建邮箱。
2. ElevenLabs 接受注册提交。
3. 收到并打开合法验证链接。
4. 使用保存的邮箱密码登录成功。
5. 页面进入 `/app/home` 或明确 onboarding。
6. 凭据文件写入本地 Docker 卷，日志中没有密码或验证参数。

若任一步失败，应保留脱敏 phase 日志并针对该阶段修复，不应自动无限重试或切换未知代理。
