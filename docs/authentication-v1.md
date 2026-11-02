# ScholarSprout 网站认证 V1.0（首批实现）

本文档描述当前首批代码提供的能力、配置方式和已知边界。当前实现提供邮箱 OTP、GitHub OAuth、服务端 Session 的认证基础设施；**它还不代表现有科研业务 API 已经完成多用户隔离，不能据此直接开放公网注册。**

## 1. 当前实现

- `auth_users`：统一用户记录。
- `auth_identities`：邮箱和 GitHub 身份映射到统一 `user_id`。
- `auth_email_otps`：邮箱验证码摘要、过期时间、尝试次数和发送 IP。
- `auth_sessions`：服务端 Session，只在浏览器保存随机不透明 token，数据库保存 HMAC 摘要。
- `auth_oauth_states`：GitHub OAuth 一次性 state。
- 邮箱验证码有效期 10 分钟、6 位数字、最多 5 次验证尝试；同一邮箱 60 秒内只能申请一次，同一 IP 每小时最多 10 次。
- GitHub OAuth 使用 `read:user user:email` scope，只选用 GitHub 返回的已验证主邮箱作为资料字段；不会仅凭相同邮箱自动合并已有账号。
- Session Cookie 为 HttpOnly、SameSite=Lax；生产环境应启用 Secure。

认证表与现有 `research.sqlite3` 共用 SQLite 文件，后续可在同一数据库中进行业务数据归属迁移。

## 2. 本地开发配置（PowerShell）

先在项目根目录、已激活 `novicesynapse` Conda 环境的 PowerShell 中设置环境变量。下面的变量只对当前 PowerShell 会话及其启动的子进程生效：

```powershell
$env:AUTH_ENABLED = "1"
$env:AUTH_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
$env:AUTH_COOKIE_SECURE = "0"
$env:AUTH_SESSION_DAYS = "14"

$env:SMTP_HOST = "你的 SMTP 服务器地址"
$env:SMTP_PORT = "587"
$env:SMTP_USERNAME = "你的 SMTP 用户名"
$env:SMTP_PASSWORD = "你的 SMTP 授权码或应用专用密码"
$env:SMTP_FROM = "科研萌芽 <no-reply@你的发信域名>"
$env:SMTP_TIMEOUT = "15"
```

`AUTH_SECRET_KEY` 必须长期保持不变；更换它会使已有验证码摘要和 Session 失效。不要把真实密钥提交到 Git，也不要把它粘贴到聊天或截图中。

SMTP 应使用服务商提供的 SMTP 主机、端口和专用授权码。普通邮箱登录密码通常不能直接用于 SMTP。正式发信还应为发信域名配置 SPF、DKIM，必要时配置 DMARC。

启动服务：

```powershell
python -m pip install -e .
scholarsprout gateway --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/login`。输入邮箱，点击“获取验证码”，从邮箱取回 6 位验证码后登录。首次成功验证会创建账号，后续会登录到同一邮箱身份对应的用户。

> 本地 HTTP 测试才设置 `AUTH_COOKIE_SECURE=0`。生产 HTTPS 必须设置 `AUTH_COOKIE_SECURE=1`。

## 3. GitHub OAuth 配置

在 GitHub Developer Settings 中创建 OAuth App：

- Homepage URL：本地测试可填写 `http://127.0.0.1:8000`。
- Authorization callback URL：必须与下面的 `GITHUB_CALLBACK_URL` 完全一致。

PowerShell 设置：

```powershell
$env:GITHUB_CLIENT_ID = "GitHub OAuth App 的 Client ID"
$env:GITHUB_CLIENT_SECRET = "GitHub OAuth App 的 Client Secret"
$env:GITHUB_CALLBACK_URL = "http://127.0.0.1:8000/api/auth/github/callback"
```

重启 Gateway 后，在登录页点击“使用 GitHub 登录”。

如果通过 SSH 隧道访问，浏览器实际访问地址例如 `http://127.0.0.1:18000`，那么 Homepage URL 和 Callback URL 也应使用浏览器可访问的 `127.0.0.1:18000`，并确保隧道在 OAuth 跳转期间保持连接。GitHub OAuth 的回调地址必须与注册应用中设置的地址一致。

正式部署时，使用网站 HTTPS 域名，例如：

```text
https://你的域名/api/auth/github/callback
```

同时把 `AUTH_COOKIE_SECURE` 设置为 `1`。

## 4. 已提供的 API

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/auth/email/request` | 发送邮箱验证码 |
| POST | `/api/auth/email/verify` | 验证验证码并创建 Session |
| GET | `/api/auth/github/start` | 开始 GitHub OAuth |
| GET | `/api/auth/github/callback` | OAuth 回调 |
| GET | `/api/auth/me` | 查询当前登录状态 |
| POST | `/api/auth/logout` | 撤销当前 Session |
| GET | `/login` | 登录页面 |

成功登录后浏览器收到 `ss_session` HttpOnly Cookie。不要在前端 JavaScript 中读取或保存 Session token。

## 5. 重要安全边界：暂不能公网开放

当前 Gateway 的既有科研业务接口和存储仍主要按本地单用户模式设计。尤其 WebChannel 仍接受请求体中的 `user_id`，资料库、论文文件、会话、报告和部分任务接口尚未全部改成从可信 Session 获取 owner。**前端隐藏页面或仅新增登录接口，不会自动保护这些 API。**

因此当前代码阶段的正确用途是：

1. 在本机验证邮箱和 GitHub 登录流程；
2. 检查用户、身份、Session 表是否正常写入；
3. 后续逐项迁移业务数据模型并给每个读取、修改、删除、下载接口增加服务端归属检查；
4. 完成双用户越权测试后，才考虑公网开放。

历史本地数据不能在没有可靠归属证据时自动分配给第一个注册用户。GitHub 身份与邮箱账号也必须通过已登录状态下的明确绑定流程关联；当前首批代码尚未提供账号绑定 API。

## 6. 生产部署提醒

- `AUTH_ENABLED=1` 时必须配置长度至少 32 字符的 `AUTH_SECRET_KEY`。
- SMTP、GitHub Secret 和 Session Secret 只放服务器环境变量或权限受控的 EnvironmentFile，不放前端、不提交仓库。
- 生产环境使用 HTTPS、Secure Cookie、反向代理可信 Host 配置。
- 当前邮箱发送为同步 SMTP 调用，正式高流量阶段应迁移到任务队列，并补充验证码发送监控。
- 需要补充账号绑定、CSRF 防护策略、审计日志、Session 清理任务、隐私政策与账号删除流程。
