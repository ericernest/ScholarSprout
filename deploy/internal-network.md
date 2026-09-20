# 内网部署说明

## 启动

在 `tql` 的仓库目录执行：

```bash
bash deploy/start_internal.sh
```

脚本默认让 Gateway 监听 `0.0.0.0:8000`。同一内网设备访问：

```text
http://192.168.1.5:8000/
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
curl http://192.168.1.5:8000/health
```

也可以直接使用现有 CLI：

```bash
scholarsprout gateway --host 0.0.0.0 --port 8000
```

## 配置接口的安全边界

`/settings` 页面可以从内网打开，但 `/api/config` 默认只接受服务器本机请求。这是为了避免 API Key 配置接口被网络用户访问。

推荐先通过 SSH 在服务器上配置：

```bash
scholarsprout config
```

如果确实需要从可信内网浏览器配置，可以临时启用：

```bash
SCHOLARSPROUT_ALLOW_REMOTE_CONFIG=1 bash deploy/start_internal.sh
```

配置完成后应重启服务并去掉这个环境变量。

## 网关配置完成后的公网访问

如果管理员配置：

```text
117.55.234.5:8000 -> 192.168.1.5:8000
```

应用启动方式不需要改变，公网访问地址为：

```text
http://117.55.234.5:8000/
```

端口映射不会自动提供 HTTPS 或身份认证。正式开放前需要增加认证、HTTPS 和访问控制。

## 检查清单

服务器本机：

```bash
curl http://127.0.0.1:8000/health
ss -lntp | grep ':8000'
```

内网客户端：

```bash
curl http://192.168.1.5:8000/health
```

公网客户端：

```bash
curl http://117.55.234.5:8000/health
```

公网映射只需要网关管理员配置；应用代码不需要再次修改。
