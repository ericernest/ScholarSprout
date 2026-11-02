# 邮箱+GitHub登录及多用户隔离交接文档

> 适用范围：已集成邮箱验证码登录、GitHub OAuth 登录，以及对话、领域入门、论文精读、论文管理、文件夹、论文笔记、PDF 高亮/注释等多用户隔离能力的 ScholarSprout 版本。

## 1. 目标与当前架构

ScholarSprout 在同一个 `research.sqlite3` 数据库中支持多个独立用户。服务器端通过登录 Session 确定可信 `user_id`，业务接口不应信任浏览器自行提交的 `user_id`。

当前隔离范围：

- 对话与消息
- 领域入门
- 论文精读 Session
- 论文库用户状态
- 论文文件夹
- 论文笔记
- PDF 高亮与 Annotation

底层 canonical paper / PDF 可以被多个用户安全复用，但用户私有状态彼此隔离。

GitHub 登录与邮箱登录当前是两个独立身份。系统不会因为邮箱地址相同而自动合并账号。

同一个浏览器 Profile 对同一站点通常只保留一个当前登录 Session。需要同时测试两个账号时，请使用不同浏览器 Profile、普通窗口 + 无痕窗口，或两个不同浏览器。

---

## 2. 环境准备

推荐：

- Python 3.11+
- Conda 环境：`novicesynapse`
- Windows PowerShell（本地开发）
- Git

进入项目目录后激活环境：

```powershell
conda activate novicesynapse
```

安装/刷新项目：

```powershell
python -m pip install -e . --no-deps
```

如果是第一次安装且依赖尚未安装，可以使用：

```powershell
python -m pip install -e .
```

验证 CLI：

```powershell
Get-Command scholarsprout
scholarsprout --help
```

---

## 3. 认证环境变量

### 3.1 必需配置

Hosted / 多用户模式必须设置：

```text
AUTH_ENABLED=1
AUTH_SECRET_KEY=<长期稳定的随机密钥，至少 32 字符>
AUTH_COOKIE_SECURE=0 或 1
AUTH_SESSION_DAYS=14
```

含义：

| 变量 | 用途 |
|---|---|
| `AUTH_ENABLED` | `1` 开启多用户认证；`0` 保留本地单用户兼容模式 |
| `AUTH_SECRET_KEY` | Session 签名密钥；必须长期保持稳定 |
| `AUTH_COOKIE_SECURE` | 本地 HTTP 用 `0`；生产 HTTPS 用 `1` |
| `AUTH_SESSION_DAYS` | Session 有效天数，默认 14 |

不要把真实 `AUTH_SECRET_KEY` 写进 Git、README、Issue、日志或截图。

生成新的开发密钥：

```powershell
$env:AUTH_SECRET_KEY = (& python -c "import secrets; print(secrets.token_urlsafe(48))").Trim()
```

Windows 本地开发可以保存为当前用户环境变量：

```powershell
[Environment]::SetEnvironmentVariable(
    "AUTH_SECRET_KEY",
    $env:AUTH_SECRET_KEY,
    [EnvironmentVariableTarget]::User
)
```

新开的 PowerShell 会自动继承用户级环境变量。当前窗口如果需要立即加载：

```powershell
$env:AUTH_SECRET_KEY = [Environment]::GetEnvironmentVariable(
    "AUTH_SECRET_KEY",
    [EnvironmentVariableTarget]::User
)
```

只检查是否配置，不打印真实值：

```powershell
Write-Host "AUTH_SECRET_KEY configured =" ([bool]$env:AUTH_SECRET_KEY)
Write-Host "AUTH_SECRET_KEY length =" $env:AUTH_SECRET_KEY.Length
```

---

## 4. 邮箱验证码登录（SMTP）

需要设置：

```text
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD
SMTP_FROM
SMTP_TIMEOUT
```

例如使用支持 STARTTLS 的 SMTP 服务时：

```powershell
$env:SMTP_HOST = "smtp.example.com"
$env:SMTP_PORT = "587"
$env:SMTP_USERNAME = "your-account@example.com"
$env:SMTP_FROM = "your-account@example.com"
$env:SMTP_TIMEOUT = "15"
```

`SMTP_PASSWORD` 应使用邮件服务提供的授权码/应用密码，而不是提交到仓库：

```powershell
$env:SMTP_PASSWORD = "<SMTP application password>"
```

建议只检查是否存在：

```powershell
Write-Host "SMTP configured =" ([bool]$env:SMTP_PASSWORD)
```

---

## 5. GitHub OAuth 登录

需要设置：

```text
GITHUB_CLIENT_ID
GITHUB_CLIENT_SECRET
GITHUB_CALLBACK_URL
```

本地开发回调：

```text
http://127.0.0.1:8000/api/auth/github/callback
```

PowerShell 示例：

```powershell
$env:GITHUB_CLIENT_ID = "<GitHub OAuth Client ID>"
$env:GITHUB_CLIENT_SECRET = "<GitHub OAuth Client Secret>"
$env:GITHUB_CALLBACK_URL = "http://127.0.0.1:8000/api/auth/github/callback"
```

GitHub OAuth App 中配置的 callback URL 必须与这里一致。

生产环境请改成真实 HTTPS 域名，例如：

```text
https://your-domain.example/api/auth/github/callback
```

---

## 6. 本地开发推荐配置

本地通过 `http://127.0.0.1:8000` 启动时：

```powershell
$env:AUTH_ENABLED = "1"
$env:AUTH_COOKIE_SECURE = "0"
$env:AUTH_SESSION_DAYS = "14"
```

确认：

```powershell
Write-Host "AUTH_ENABLED =" $env:AUTH_ENABLED
Write-Host "AUTH_COOKIE_SECURE =" $env:AUTH_COOKIE_SECURE
Write-Host "AUTH_SECRET_KEY =" ([bool]$env:AUTH_SECRET_KEY)
Write-Host "SMTP_PASSWORD =" ([bool]$env:SMTP_PASSWORD)
Write-Host "GITHUB_CLIENT_ID =" ([bool]$env:GITHUB_CLIENT_ID)
Write-Host "GITHUB_CLIENT_SECRET =" ([bool]$env:GITHUB_CLIENT_SECRET)
```

Hosted 测试时不应在 Session 无效后自动回退到 `local-web`。

---

## 7. 启动 ScholarSprout

标准启动命令：

```powershell
scholarsprout gateway --host 127.0.0.1 --port 8000
```

成功时应出现类似：

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8000
```

登录页：

```text
http://127.0.0.1:8000/login
```

如果 `scholarsprout` 命令找不到，先检查：

```powershell
Get-Command scholarsprout
where.exe scholarsprout
where.exe python
```

然后刷新 editable install：

```powershell
python -m pip install -e . --no-deps
```

正常情况下无需设置永久 `PYTHONPATH`。

---

## 8. 数据目录与数据库

默认数据目录由 ScholarSprout 配置解析。可使用：

```powershell
python -c "from config.manager import load_config, resolve_data_dir; p=resolve_data_dir(load_config()); print(p / 'research.sqlite3')"
```

查看当前数据库位置。

数据库初始化由应用启动时自动完成。多用户版本会维护用户私有状态表和 ownership/access 信息。

### 历史数据原则

升级前创建的旧数据如果无法可靠证明 owner，不会自动分配给第一个登录用户。

在 `AUTH_ENABLED=1` 时：

- 已明确属于当前用户的数据：可见
- 属于其他用户的数据：不可见/拒绝访问
- 无法证明 owner 的 legacy 数据：默认隐藏

这是安全设计，不代表数据被删除。

不要通过直接修改 SQLite 的 `user_id` 来“修复”归属，除非有明确的数据迁移方案和数据库备份。

---

## 9. 多用户隔离模型

### 对话

每个 conversation 绑定服务器端可信用户身份。列表、详情、fork、context artifact 链接等都按 owner 检查。

### 领域入门

领域入门 workspace、持久化结果及运行中的 job 均按用户身份限制访问。

### 论文精读

reading session、继续/分叉/合并及相关论文资源访问按用户授权检查。

### 论文管理

底层 canonical paper/PDF 可复用，但以下状态为用户私有：

- library membership/status
- 文件夹
- library note
- Markdown 论文笔记
- PDF highlight
- annotation note

因此两个用户导入同一篇论文时，可以复用底层 PDF，但不会看到彼此的笔记、高亮或文件夹状态。

---

## 10. 双账号验收方法

不要在同一个浏览器 Profile 中同时测试两个账号，因为后一次登录会更新同站点 Session Cookie。

推荐：

```text
Chrome Profile A / 普通窗口 → GitHub 用户 A
Edge / Chrome InPrivate     → 邮箱用户 B
```

验收至少覆盖：

1. A 创建对话，B 看不到。
2. B 创建对话，A 看不到。
3. A 创建领域入门，B 看不到。
4. B 创建领域入门，A 看不到。
5. A 开始论文精读，B 看不到 A 的 reading session。
6. A/B 同时导入相同论文。
7. A 新建文件夹、笔记、高亮、annotation。
8. B 能使用同一篇论文，但看不到 A 的上述私有状态。
9. B 的修改不能改变或删除 A 的私有状态。

---

## 11. 回归测试

认证：

```powershell
python -m unittest discover tests/test_auth -v
```

对话隔离：

```powershell
python -m unittest tests.storage.test_conversation_isolation -v
```

领域入门 / 论文精读：

```powershell
python -m unittest tests.storage.test_research_mode_isolation -v
```

论文库 / 文件夹 / 笔记 / 高亮：

```powershell
python -m unittest tests.storage.test_paper_library_isolation -v
```

当前版本预期这些测试全部通过。

---

## 12. 本地免登录兼容模式

如果明确需要原来的本地单用户行为：

```powershell
$env:AUTH_ENABLED = "0"
```

此模式用于兼容本地桌面/单用户工作流，不等价于 Hosted 多用户安全模式。

多人网站或任何开放网络环境必须使用：

```text
AUTH_ENABLED=1
```

并正确配置长期稳定的 `AUTH_SECRET_KEY`。

---

## 13. 生产部署注意事项

生产环境至少应满足：

- HTTPS
- `AUTH_ENABLED=1`
- `AUTH_COOKIE_SECURE=1`
- 稳定且保密的 `AUTH_SECRET_KEY`
- GitHub callback 使用生产 HTTPS 域名
- SMTP Secret、GitHub Client Secret 不进入 Git
- 数据库定期备份
- 部署前运行全部隔离测试
- 不把 `.env`、SQLite 数据库、日志、安装备份目录提交到仓库

生产 Secret 建议由服务器环境或专用 secrets 管理机制注入，而不是写在源码目录中。

---

## 14. 不应提交到 Git 的内容

至少包括：

```text
.env
.env.*
*.sqlite3
*.db
*.bak
__pycache__/
.pytest_cache/
.vscode/
*.log
```

以及本次开发过程中产生的本地升级/备份目录，例如：

```text
.conversation-isolation-backup-*/
.research-mode-isolation-backup-*/
.paper-library-isolation-backup-*/
.cli-bootstrap-backup-*/

_conversation_isolation_exact/
_research_mode_isolation_exact/
_paper_library_isolation_exact/
_cli_bootstrap_exact/
```

真实密码、授权码、OAuth Secret、Session Secret 绝对不能提交。

如需提供配置模板，请使用不含真实 Secret 的 `.env.example` 或 `deploy/auth.env.example`。

---

## 15. 协作修改原则

后续新增任何“用户私有”科研功能时，应遵循：

1. 用户身份必须从服务器端 Session 获取。
2. 不信任客户端提交的 `user_id`。
3. 列表、详情、更新、删除都必须做 owner/access 检查。
4. 跨用户资源应返回拒绝访问或 404，避免泄漏资源是否存在。
5. 新功能必须增加 A/B 双用户隔离测试。
6. 对历史无 owner 数据采用 fail-closed，不自动认领。
7. canonical 可共享数据与用户私有状态应分层建模。

这样可以避免以后新增模块重新引入跨账号数据泄漏。
