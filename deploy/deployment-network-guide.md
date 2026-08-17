# NoviceSynapse 部署指南

## 1. 启动服务

登录 `tql`，在仓库目录执行：

```bash
cd ~/competition/NoviceSynapse
NOVICESYNAPSE_PYTHON=/data2/sxf2026/miniconda3/envs/novicesynapse/bin/python bash deploy/start_campus.sh
```

看到下面的日志表示服务启动成功：

```text
Uvicorn running on http://0.0.0.0:8000
```

`0.0.0.0:8000` 表示服务监听服务器所有网卡的 TCP 8000 端口，不是浏览器访问地址。

## 2. 服务器侧配置

```text
服务器内网 IP：192.168.1.5
服务端口：TCP 8000
```

检查服务：

```bash
ss -lntp | grep ':8000'
curl --noproxy '*' http://192.168.1.5:8000/health
```

正常应返回：

```json
{"status":"ok","service":"novicesynapse-gateway"}
```

如果服务器启用了主机防火墙，需要允许：

```text
校园网用户网段 -> 192.168.1.5:8000/TCP
```

UFW 规则示例：

```bash
ufw allow from <校园网用户网段> to 192.168.1.5 port 8000 proto tcp
ufw status numbered
```

## 3. 校园网访问配置

如果校园网不能直接访问 `192.168.1.5:8000`，需要在负责 `210.45.71.96` 的网关上分配一个可用 TCP 端口，并转发到服务器的 TCP 8000。例如：

```text
210.45.71.96:18000 -> 192.168.1.5:8000/TCP
```

其中 `18000` 只是示例，实际使用管理员分配的未占用端口。配置完成后，校园网用户访问：

```text
http://210.45.71.96:18000/
```

健康检查：

```text
http://210.45.71.96:18000/health
```

程序不需要修改，仍然监听 `0.0.0.0:8000`。

## 4. 客户端测试

Windows PowerShell：

```powershell
Test-NetConnection 210.45.71.96 -Port 18000
curl.exe --noproxy "*" http://210.45.71.96:18000/health
```

Linux：

```bash
curl --noproxy '*' http://210.45.71.96:18000/health
```

将示例端口 `18000` 替换为管理员实际分配的端口。
