# 公网访问验证

本目录只负责部署探测和反向访问验证，不包含 OAuth，也不替代正式应用部署。

完整的服务启动、SSH/VS Code 端口转发、校园网访问和网关端口配置说明见 [`deployment-network-guide.md`](deployment-network-guide.md)。

## 检查学校服务器

```bash
python3 deploy/public_access.py check --port 8000
```

它会检查公网出口 IP、外部 HTTPS 出站能力和本地端口监听状态。

指定中继服务器健康检查地址：

```bash
python3 deploy/public_access.py check \
  --port 8000 \
  --probe-url https://github.com \
  --probe-url https://your-relay.example.com/health
```

没有 `curl` 时也可以直接执行：

```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('https://api.ipify.org', timeout=8).read().decode())"
```

## 验证 SSH 反向隧道

这需要一台有公网地址的中继服务器。学校服务器先启动测试服务：

```bash
python3 -m http.server 8000 --bind 127.0.0.1
```

再从学校服务器建立反向隧道：

```bash
ssh -N -o ExitOnForwardFailure=yes -R 18000:127.0.0.1:8000 user@public-server
```

在公网服务器上测试：

```bash
curl http://127.0.0.1:18000
```

如果能看到目录页面，说明学校服务器主动出站到公网服务器的链路可用。

## 验证公网 IP 是否允许入站

在另一台外部机器测试，不能只在学校服务器本机测试：

Linux/macOS：

```bash
nc -vz SCHOOL_PUBLIC_IP 8000
```

Windows PowerShell：

```powershell
Test-NetConnection SCHOOL_PUBLIC_IP -Port 8000
```

失败通常表示存在校园网 NAT、服务器防火墙、上游防火墙或学校网络策略限制。

## 临时 Quick Tunnel

如果服务器允许运行 `cloudflared`，可以临时测试：

```bash
python3 deploy/public_access.py tunnel --port 8000
```

但 Quick Tunnel 只适合开发测试，不作为正式方案；官方限制包括临时域名、并发限制，并且不支持 SSE。

## `client.py` 参考架构

参考的 `client.py` 是“学校服务器客户端 + 公网 WebSocket 中继端”。它需要同时部署中继端，不能只运行客户端。正式接入前还需要确定中继地址、鉴权 token、目标端口以及 SSE/WebSocket 转发规则。
