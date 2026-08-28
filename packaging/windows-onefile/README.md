# Windows 免安装版

这里保存“研见 · SeeFurther”Windows 免安装程序的可复现打包入口。默认生成
启动更快的目录版，以及可直接分发的 ZIP；构建产物已被 Git 忽略。

## 生成

在仓库根目录执行：

```powershell
.\packaging\windows-onefile\build.ps1 -InstallBuildTool
```

之后如果隔离环境已经准备好，可省略 `-InstallBuildTool`。默认产物为：

- `output/SeeFurther/`：包含 `SeeFurther.exe` 及所需 DLL、PYD 和资源；
- `output/SeeFurther-windows-x64.zip`：上述完整目录的可分发压缩包。

目录版不需要在每次启动时把运行环境解压到临时目录，因此启动明显快于单文件版。
分发时必须复制整个 `SeeFurther` 文件夹，不能只拿出其中的 exe；或者直接发送 ZIP，
使用者完整解压后双击 `SeeFurther.exe` 即可。

如确实需要单个 exe，可执行：

```powershell
.\packaging\windows-onefile\build.ps1 -Mode OneFile
```

也可用 `-Mode Both` 同时生成两种布局。打包内容均包括网关、静态资源、内置
Skills、PDF 解析依赖和飞书 SDK。

脚本会在当前目录创建被 Git 忽略的 `.venv` 隔离构建环境，不会卸载或改写全局
Python/Conda 环境中的包。

## 使用

双击 `output/SeeFurther/SeeFurther.exe`。程序优先监听 `127.0.0.1:8000`；如果端口已占用，
会在 `8001-8099` 中选择空闲端口，并在服务就绪后打开浏览器。

程序运行后会在 Windows 通知区域显示与网页一致的极光图标。双击图标或选择
“打开研见”可重新打开页面；选择“退出研见”会先正常停止本地网关，再退出程序。

模型配置、论文和会话数据库仍保存在当前 Windows 用户的 `.novicesynapse` 数据
目录中，不会写到 exe 内部。首次启动仍需在研见页面完成模型配置。

如果程序在打开浏览器之前启动失败，可查看
`%LOCALAPPDATA%\SeeFurther\launcher-error.log` 中的完整错误信息。

## 边界

- 这是 Windows x64 免安装构建；Linux 不能直接运行该 `.exe`，需要在 Linux 上
  继续使用源码部署，或在 Linux 环境重新生成对应平台的可执行文件。
- Windows Defender/SmartScreen 可能对未签名的新 exe 给出提示。正式分发时应使用
  代码签名证书签名；这不影响本地功能验证。
