# Git 与 GitHub 入门教程 — NoviceSynapse 项目专用

> **面向读者**: 零基础、从未使用过 Git 的开发者  
> **目标**: 从安装 Git 到成功将论文精读代码推送到 GitHub  
> **创建日期**: 2026-07-20

---

## 目录

1. [Git 是什么？为什么要用？](#1-git-是什么为什么要用)
2. [安装 Git](#2-安装-git)
3. [核心概念速览](#3-核心概念速览)
4. [第一步：把项目变成 Git 仓库](#4-第一步把项目变成-git-仓库)
5. [日常操作：新增文件和修改文件后如何提交](#5-日常操作新增文件和修改文件后如何提交)
6. [如何区分"新增文件"和"修改已有文件"](#6-如何区分新增文件和修改已有文件)
7. [连接 GitHub 并推送](#7-连接-github-并推送)
8. [分支工作流（对应项目规范）](#8-分支工作流对应项目规范)
9. [论文精读模块——实际提交流程演示](#9-论文精读模块实际提交流程演示)
10. [常见问题速查](#10-常见问题速查)
11. [命令速查表](#11-命令速查表)

---

## 1. Git 是什么？为什么要用？

### 一句话解释

**Git 是「代码的时光机」**——它可以记录你每一次代码修改的快照，让你随时回到过去的任意版本，也能多人协作而不会互相覆盖对方的代码。

### 类比理解

想象你写论文时的 Word 文件：

```
论文_v1.docx
论文_v1_导师修改.docx
论文_v2.docx
论文_v2_最终版.docx
论文_v2_真的最终版.docx
论文_v3.docx  ← 当前版本
```

Git 做的事情就是：**自动帮你管理所有这些版本**，不用手动复制文件，也不需要 `_v1` `_v2` `_最终版` 这样的命名。你只需写一句"这次改了什么"，Git 就帮你存一份快照。

### GitHub 呢？

- **Git** = 你本地电脑上的版本管理工具
- **GitHub** = 网上的代码托管平台，用于备份你的代码、和他人协作

```
你的电脑 (Git)  ←──推送(push)──→  GitHub (云端备份)
```

---

## 2. 安装 Git

### Windows

1. 打开浏览器，访问 https://git-scm.com/download/win
2. 下载 `.exe` 安装程序，双击运行
3. 安装过程中**全部保持默认选项**，一直点 "Next" 即可
4. 安装完成后，在任意文件夹中**右键** → 选择 **"Open Git Bash here"** → 出现命令行窗口即为安装成功

验证安装：

```bash
# 在 Git Bash 中输入
git --version
# 应该输出类似: git version 2.47.0
```

### 配置用户信息（仅需一次）

```bash
# 在 Git Bash 中执行，替换为你的信息
git config --global user.name "你的名字"
git config --global user.email "你的邮箱@example.com"

# 验证
git config --global user.name
git config --global user.email
```

---

## 3. 核心概念速览

Git 有 **4 个位置**，你需要理解文件在它们之间移动的路径：

```
┌──────────────┐     git add      ┌──────────────┐     git commit     ┌──────────────┐     git push      ┌──────────────┐
│   工作目录    │  ───────────────→ │   暂存区      │  ────────────────→ │   本地仓库     │  ────────────────→ │   远程仓库    │
│  (你的文件)   │                  │  (准备提交)    │                    │  (版本历史)    │                   │  (GitHub)     │
└──────────────┘                  └──────────────┘                    └──────────────┘                   └──────────────┘
                                                                           ↑
                                                                     git pull (拉取远程更新)
```

| 概念 | 白话解释 |
|------|---------|
| **工作目录 (Working Directory)** | 你电脑上的项目文件夹，就是你写代码的地方 |
| **暂存区 (Staging Area)** | 一个"购物车"，你把这次要提交的文件放进去 |
| **本地仓库 (Local Repository)** | 你电脑上的版本历史库，存在 `.git` 隐藏文件夹里 |
| **远程仓库 (Remote Repository)** | GitHub 上的版本库 |
| **commit (提交)** | 拍一张代码快照，记录"这次改了什么" |
| **push (推送)** | 把本地的提交同步到 GitHub |
| **pull (拉取)** | 把 GitHub 上的更新同步到本地 |
| **branch (分支)** | 一条独立的开发线，不影响主线代码 |
| **git status** | 查看「哪些文件改了、哪些文件是新的」 |
| **git diff** | 查看「具体改了什么内容」 |

---

## 4. 第一步：把项目变成 Git 仓库

你的项目目前**还不是 Git 仓库**——这就是为什么 `git status` 会报错 `not a git repository`。

### 4.1 初始化仓库

```bash
# 1. 进入项目目录
cd "d:/2026-2027项目-论文-竞赛/NoviceSynapse-develop/NoviceSynapse-develop"

# 2. 初始化 Git 仓库（只需做一次）
git init

# 输出: Initialized empty Git repository in ...
```

执行后，项目目录下会多出一个 **隐藏文件夹 `.git`**。这个文件夹存储了所有的版本历史——**永远不要手动修改或删除它**。

### 4.2 检查是否有 `.gitignore` 文件

`.gitignore` 告诉 Git **哪些文件不需要追踪**（如临时文件、虚拟环境、日志等）：

```bash
# 检查是否已有 .gitignore
ls -la .gitignore
```

如果项目还没有 `.gitignore`，创建一个：

```bash
# 在项目根目录创建 .gitignore 文件
cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/

# 虚拟环境
venv/
env/
.venv/

# IDE
.vscode/
.idea/
*.swp
*.swo

# 系统文件
.DS_Store
Thumbs.db

# 环境变量
.env
.env.local

# 日志
*.log

# Jupyter
.ipynb_checkpoints/
EOF
```

### 4.3 第一次提交（全量提交）

```bash
# 步骤 1: 查看当前状态
git status

# 步骤 2: 把所有文件加入暂存区
git add .

# 步骤 3: 提交
git commit -m "初始提交: 项目框架 + 论文精读模块骨架代码"

# 步骤 4: 查看提交历史
git log --oneline
```

---

## 5. 日常操作：新增文件和修改文件后如何提交

### 5.1 核心三步流程

每次修改代码后，都走这三步：

```bash
# ① 添加改动的文件到暂存区
git add <文件名>        # 添加特定文件
git add .              # 添加当前目录下所有改动

# ② 提交，写清楚「这次改了什么」
git commit -m "一句话描述你的修改"

# ③ (如果有远程仓库) 推送到 GitHub
git push
```

### 5.2 示例

```bash
# 修改了 handler.py，新增了 skill.py
git add paper_reading/handler.py paper_reading/skills/method_analyst.py
git commit -m "feat: 实现方法论分析师 skill 的业务逻辑后处理"
git push
```

### 5.3 写好 commit message 的规则

提交信息用英文或中文都可以，但格式要一致。推荐格式：

```
<类型>: <简短描述>

类型:
  feat      — 新功能
  fix       — 修复 bug
  docs      — 文档修改
  refactor  — 代码重构（不改变功能）
  test      — 测试相关
  chore     — 杂项（依赖更新、配置修改等）

示例:
  feat: 新增论文搜索多源去重逻辑
  fix: 修复 PDF 解析器中文编码问题
  docs: 添加 Git 入门教程文档
```

---

## 6. 如何区分"新增文件"和"修改已有文件"

这是你最关心的问题。**`git status`** 会清楚地告诉你一切。

### 6.1 看懂 git status

```bash
git status
```

典型输出示例：

```
On branch main

Changes not staged for commit:         ← 修改了已有文件，但还没加到暂存区
  (use "git add <file>..." to update what will be committed)

        modified:   gateway/app.py     ← 修改了已有文件
        modified:   tools/registry.py  ← 修改了已有文件

Untracked files:                       ← 全新的文件，Git 之前从未见过
  (use "git add <file>..." to include in what will be committed)

        paper_reading/                 ← 整个新目录
        skills/builtin/reading/        ← 整个新目录
        tools/builtin/paper_search_tool.py  ← 新文件
```

**解读**:

| git status 显示 | 含义 | 操作 |
|----------------|------|------|
| `modified: xxx.py` | 已有文件被修改了 | `git add xxx.py` 加入暂存区 |
| `Untracked files: xxx` | 全新文件，Git 还没追踪 | `git add xxx` 开始追踪 |
| `new file: xxx.py` | 新文件已加入暂存区，等待提交 | `git commit` 提交 |
| `deleted: xxx.py` | 文件被删除了 | `git add xxx.py` (确认删除) 或恢复 |

### 6.2 查看具体改了什么内容

```bash
# 查看所有修改（还没 git add 之前）
git diff

# 查看某个文件的具体改动
git diff gateway/app.py

# 查看已 git add 但还没 commit 的改动
git diff --staged

# 查看某次提交改了哪些文件
git show --name-only <commit-id>

# 查看某次提交的具体内容
git show <commit-id>
```

`git diff` 的输出中：
- **红色**（前面有 `-`）= 删掉的内容
- **绿色**（前面有 `+`）= 新增的内容

### 6.3 实战：论文精读模块的文件分类

针对你本次的开发，打开 Git Bash 执行 `git status`，你会看到：

**新增文件 (Untracked)**——这些都是你这次全新创建的：

```
paper_reading/__init__.py
paper_reading/handler.py
paper_reading/schemas/__init__.py
paper_reading/schemas/request.py
paper_reading/schemas/response.py
paper_reading/pipeline/__init__.py
paper_reading/pipeline/metadata.py
paper_reading/pipeline/parser.py
paper_reading/pipeline/sources.py
paper_reading/kg/__init__.py
paper_reading/kg/models.py
paper_reading/kg/engine.py
paper_reading/kg/builder.py
paper_reading/kg/fusion.py
paper_reading/harness/__init__.py
paper_reading/harness/storage.py
paper_reading/harness/session.py
paper_reading/harness/fork_merge.py
paper_reading/harness/progress.py
paper_reading/skills/__init__.py
skills/builtin/reading/method_analyst/SKILL.md
skills/builtin/reading/critique_agent/SKILL.md
skills/builtin/reading/math_verifier/SKILL.md
skills/builtin/reading/code_reviewer/SKILL.md
skills/builtin/reading/domain_expert/SKILL.md
skills/builtin/reading/writing_coach/SKILL.md
skills/builtin/reading/idea_generator/SKILL.md
skills/builtin/reading/cross_paper_linker/SKILL.md
tools/builtin/paper_search_tool.py
tools/builtin/pdf_parse_tool.py
tools/builtin/kg_query_tool.py
tools/builtin/kg_build_tool.py
tests/test_paper_reading/verify_imports.py
docs/论文精读-实现计划.md
docs/论文精读-开发工作文档.md
```

**修改的已有文件 (Modified)**——这些文件原来就存在，你往里面加了内容：

```
handlers/paper_reading_handler.py    ← 原来是占位符，重写了
agents/profiles.json                 ← 新增了 paper_reading 条目
gateway/app.py                       ← 新增了 7 个组件初始化
tools/registry.py                    ← 新增了 4 个 tool 注册
pyproject.toml                       ← 新增了依赖和包发现
```

### 6.4 按功能分批提交（推荐）

建议不要把 40+ 个文件一次性全部提交，而是按功能模块分批：

```bash
# 第一批: 项目配置变更
git add pyproject.toml
git commit -m "chore: 新增论文精读模块依赖 (PyMuPDF/httpx/feedparser/networkx)"

# 第二批: Agent Profile + Tool 注册
git add agents/profiles.json tools/registry.py tools/builtin/paper_search_tool.py tools/builtin/pdf_parse_tool.py tools/builtin/kg_query_tool.py tools/builtin/kg_build_tool.py
git commit -m "feat: 新增 paper_reading agent profile 和 4 个论文工具"

# 第三批: Layer 1 论文流水线
git add paper_reading/pipeline/
git commit -m "feat: Layer 1 论文流水线 — 多源检索 + PDF 解析 + 元数据标准化"

# 第四批: Layer 2 知识图谱引擎
git add paper_reading/kg/
git commit -m "feat: Layer 2 知识图谱引擎 — 13节点+9边 + NetworkX + 渐进构建 + 跨论文融合"

# 第五批: Layer 3+4 + Handler
git add paper_reading/schemas/ paper_reading/harness/ paper_reading/handler.py paper_reading/__init__.py
git commit -m "feat: Layer 3/4 + Handler — 会话引擎 + Fork/Merge + 统一请求响应协议"

# 第六批: Skill 定义
git add skills/builtin/reading/
git commit -m "feat: Layer 3 Skill 体系 — 8 个论文精读 SKILL.md 定义"

# 第七批: Handler 对接 + Gateway
git add handlers/paper_reading_handler.py gateway/app.py
git commit -m "feat: Handler 重写 + Gateway 集成 — 对接 paper_reading 模块"

# 第八批: 文档 + 测试
git add docs/ tests/test_paper_reading/
git commit -m "docs: 实现计划 + 开发工作文档 + 验证脚本"
```

---

## 7. 连接 GitHub 并推送

### 7.1 在 GitHub 上创建仓库

1. 打开 https://github.com 并登录
2. 点击右上角 **"+"** → **"New repository"**
3. 填写仓库名称（如 `NoviceSynapse`）
4. **不要**勾选 "Add a README file"（因为本地已经有 README 了）
5. **不要**勾选 ".gitignore"（本地已经有了）
6. 点击 **"Create repository"**

创建后，GitHub 会显示一段命令，复制第二段（"push an existing repository"）：

```bash
git remote add origin https://github.com/你的用户名/NoviceSynapse.git
git branch -M main
git push -u origin main
```

### 7.2 推送已有提交

```bash
# 第一次推送（关联远程仓库）
git remote add origin https://github.com/你的用户名/NoviceSynapse.git
git branch -M main
git push -u origin main

# 之后每次推送只需要
git push
```

### 7.3 如果远程仓库已有内容（如初始化时勾选了 README）

```bash
# 先拉取远程内容并合并
git pull origin main --allow-unrelated-histories

# 解决可能的冲突后
git push origin main
```

### 7.4 GitHub 身份认证

推送到 GitHub 时需要证明你的身份。推荐使用 **Personal Access Token (PAT)**：

1. GitHub 右上角头像 → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**
2. 点击 **"Generate new token (classic)"**
3. 勾选 `repo` (全部) 和 `workflow`
4. 生成后**立即复制保存**（只显示一次！）
5. 推送时，用户名输入你的 GitHub 用户名，密码输入这个 Token

或者使用 GitHub CLI (`gh`) 更方便：

```bash
# 安装 GitHub CLI: https://cli.github.com/
gh auth login
# 按提示选择 HTTPS，用浏览器完成认证

# 之后推送
git push
```

---

## 8. 分支工作流（对应项目规范）

项目已有 [git-workflow.md](git-workflow.md) 定义了分支规范。简单来说：

### 分支结构

```
main        ← 稳定版本（可演示）
  ↑
develop     ← 日常开发集成
  ↑
feat/xxx    ← 你的功能开发分支
```

### 标准流程

```bash
# 1. 切换到 develop 分支（如果没有则从 main 创建）
git checkout -b develop

# 2. 从 develop 创建你的功能分支
git checkout -b feat/paper-reading-module

# 3. 在功能分支上提交所有代码
git add paper_reading/
git commit -m "feat: 论文精读模块完整实现"

# 4. 推送功能分支到 GitHub
git push -u origin feat/paper-reading-module

# 5. 去 GitHub 网页上创建 Pull Request (PR)
#    把 feat/paper-reading-module 合并到 develop

# 6. 合并完成后，切回 develop，拉取最新代码
git checkout develop
git pull origin develop

# 7. 删除已完成的功能分支（可选）
git branch -d feat/paper-reading-module
```

### 如果你一个人开发，可以简化

```bash
# 直接在 main 分支上工作（不推荐，但入门阶段可以接受）
git add .
git commit -m "你的提交信息"
git push origin main
```

---

## 9. 论文精读模块——实际提交流程演示

下面是你**从零开始**应该执行的完整 Git 操作序列：

### Step 1: 初始化仓库

```bash
# 打开 Git Bash，进入项目目录
cd "d:/2026-2027项目-论文-竞赛/NoviceSynapse-develop/NoviceSynapse-develop"

# 初始化 Git
git init
```

### Step 2: 创建 .gitignore（如果还没有）

```bash
cat > .gitignore << 'EOF'
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
venv/
.vscode/
.idea/
.DS_Store
Thumbs.db
*.log
.env
EOF
```

### Step 3: 第一次全量提交（项目原有代码）

```bash
# 查看将要提交的文件
git status

# 全部加入暂存区
git add .

# 提交
git commit -m "初始提交: NoviceSynapse 项目框架 + 论文精读模块骨架"
```

### Step 4: 查看你这次的改动总览

```bash
# 以后每次修改代码后，用这两个命令了解状态
git status              # 哪些文件变了
git diff --stat         # 每个文件改了多少行
```

### Step 5: 后续增量提交

以后你每次改完代码：

```bash
# ① 查看改了什么
git status

# 输出示例:
# modified:   paper_reading/kg/builder.py
# Untracked:  paper_reading/skills/method_analyst.py

# ② 选择要提交的文件
git add paper_reading/kg/builder.py paper_reading/skills/method_analyst.py

# ③ 提交
git commit -m "feat: 细化 KG Builder 的 Method 章节提取 prompt"

# ④ 推送到 GitHub
git push
```

### Step 6: 连接 GitHub 并推送

```bash
# 关联远程仓库（替换为你的仓库地址）
git remote add origin https://github.com/你的用户名/NoviceSynapse.git

# 推送
git branch -M main
git push -u origin main
```

---

## 10. 常见问题速查

### Q1: `git add .` 和 `git add <文件名>` 有什么区别？

- `git add .` — 把**当前目录下所有改动**都加入暂存区
- `git add <文件名>` — 只加指定的文件

**建议新手用 `git add <文件名>`**，更精确，避免不小心提交了不该提交的文件。

### Q2: 我不小心 `git add` 了一个不该提交的文件

```bash
# 从暂存区移除（文件本身不会被删除）
git reset HEAD <文件名>
```

### Q3: 我提交了但 commit message 写错了

```bash
# 修改最近一次提交的信息
git commit --amend -m "正确的提交信息"
```

### Q4: 我怎么看之前提交了什么？

```bash
# 简洁历史
git log --oneline

# 详细历史
git log

# 图形化历史（好看）
git log --oneline --graph --all
```

### Q5: 我改了文件但想恢复到之前的版本

```bash
# 丢弃工作目录的修改（恢复到最后一次 commit 的状态）
git checkout -- <文件名>

# 丢弃所有未提交的修改（危险操作！）
git checkout -- .
```

### Q6: GitHub 推送时报 `Permission denied`

这说明身份认证有问题。尝试：

```bash
# 方案 1: 检查远程地址
git remote -v

# 方案 2: 重新设置远程地址（使用 Token）
git remote set-url origin https://<你的Token>@github.com/用户名/仓库名.git

# 方案 3: 使用 GitHub CLI
gh auth login
gh auth setup-git
git push
```

### Q7: `fatal: not a git repository`

你不在 Git 仓库目录中。先 `cd` 到项目目录，或用 `git init` 初始化。

---

## 11. 命令速查表

| 场景 | 命令 |
|------|------|
| 初始化仓库 | `git init` |
| 查看状态（哪些文件改了） | `git status` |
| 查看具体改了什么 | `git diff` |
| 查看某文件改了什么 | `git diff <文件名>` |
| 加入暂存区（单个文件） | `git add <文件名>` |
| 加入暂存区（所有文件） | `git add .` |
| 从暂存区移除 | `git reset HEAD <文件名>` |
| 提交 | `git commit -m "提交信息"` |
| 修改最近提交信息 | `git commit --amend -m "新信息"` |
| 查看提交历史 | `git log --oneline` |
| 查看某次提交改了什么 | `git show <commit-id>` |
| 查看某次提交改了哪些文件 | `git show --name-only <commit-id>` |
| 关联远程仓库 | `git remote add origin <URL>` |
| 推送 | `git push` |
| 第一次推送 | `git push -u origin main` |
| 拉取远程更新 | `git pull` |
| 创建新分支 | `git checkout -b <分支名>` |
| 切换分支 | `git checkout <分支名>` |
| 查看所有分支 | `git branch -a` |
| 删除分支 | `git branch -d <分支名>` |
| 恢复文件到最后提交 | `git checkout -- <文件名>` |
| 查看谁改了什么 | `git blame <文件名>` |

---

> **记住三句话，日常就够用了**:
>
> ```bash
> git status          # 先看看改了啥
> git add <文件>      # 把要提交的放进购物车
> git commit -m "..." # 结账：拍一张快照
> git push            # 备份到 GitHub
> ```
>
> 遇到问题不要慌——Git 不会删除你的文件，任何误操作都有办法恢复。善用 `git status` 了解当前状态，是入门期最重要的习惯。
