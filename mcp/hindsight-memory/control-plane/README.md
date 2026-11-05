# Hindsight Web 管理端 · 手动前台启动

本目录维护 Win 官方 Hindsight Control Plane **1.2.0** 的安装与启动脚本，接入现有 `D:\Hindsight` API。只安装 Web UI，不新建数据库、不导入记忆、不变更现有 Hindsight API、PostgreSQL 或 BGE 服务。管理端不注册开机任务，不隐藏进程，不运行后台守护或自动恢复。

## 启动与关闭

在 Win 终端运行完整路径，或双击该脚本：

```powershell
& 'D:\Hindsight\scripts\Start-HindsightUI.cmd'
```

该窗口显示实时日志；本机浏览器打开 <http://localhost:9999>。在该窗口按 **Ctrl+C** 关闭 UI，现有后端继续运行。需要修改 UI 端口时运行：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File 'D:\Hindsight\scripts\Start-HindsightUI.ps1' -Port 9998
```

对应浏览地址为 `http://localhost:9998`。脚本直接前台运行官方包中的 `standalone/server.js`，不经过会隐藏子进程窗口的官方 CLI 包装器。`HOSTNAME=localhost` 配合 `--dns-result-order=ipv4first` 使用 IPv4 loopback；这是官方 CLI 对 loopback 的处理方式，避免 locale rewrite 重定向循环。[官方 CLI 源码](https://github.com/vectorize-io/hindsight/blob/v1.2.0/hindsight-control-plane/bin/cli.js)

## Mac 经现有局域网 SSH 浏览

先在 Win 启动 UI，再在 Mac 的可见终端运行；将占位值换成已有 Win 局域网 SSH 地址：

```bash
ssh -N -L127.0.0.1:9999:127.0.0.1:9999 admin@<WIN_LAN_HOST>
```

保持该终端开启，Mac 浏览器打开 <http://localhost:9999>。Ctrl+C 关闭 Mac SSH 隧道；Win UI 由 Win 原启动窗口的 Ctrl+C 关闭。两个窗口分别控制各自进程。修改远端 UI 端口时，同步修改 `-L` 最后一个端口；本地端口占用时可修改第一个端口及浏览地址。

UI 仅监听 Win loopback，Mac 转发也仅绑定本机 loopback；不新增局域网或公网监听，不修改防火墙，不使用 VPS 或 EasyTier。SSH 复用现有认证和主机身份核验。

## 配置与权限

启动脚本从 `D:\Hindsight\config\shared.env` 读取既有 `HINDSIGHT_API_TENANT_API_KEY`，只在当前进程环境中映射为 `HINDSIGHT_CP_DATAPLANE_API_KEY`，连接 `http://127.0.0.1:8888`。不新建凭据、不打印 key、不把 key放入命令行或前端配置；退出时恢复原进程环境。

浏览器请求同源 UI API，管理端在服务端添加后端 Bearer 认证。[官方服务端客户端源码](https://github.com/vectorize-io/hindsight/blob/v1.2.0/hindsight-control-plane/src/lib/hindsight-client.ts)说明这两个环境变量的用途。运行配置和凭据保留在 Win 私有部署目录，不提交仓库。

UI 具有现有 tenant 身份允许的管理写权，**不是只读页面**。浏览 bank、配置和既有操作无需创建新记忆；保存配置、删除、导入、提取或查询测试须由使用者主动执行。其中部分功能可能调用模型，不属于安装检查。

## 安装与维护来源

已有安装可直接使用启动脚本。新安装要求目标 `D:\Hindsight\control-plane` 不存在；先核对已有目录和端口，不覆盖未知安装。从本目录运行：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\Install-HindsightUI.ps1 -Root 'D:\Hindsight'
```

安装脚本使用现有 `npm.cmd` 和 Windows `tar.exe`：下载官方预构建包，校验固定 SHA-512 后解压到 `D:\Hindsight\control-plane\package`，将两个启动脚本复制到 `D:\Hindsight\scripts`，保存 `install-receipt.json`。不执行全量 npm 依赖安装。安装中断后先检查留下的目录，再决定恢复方式。

| 项目 | 固定来源 |
| --- | --- |
| 官方 npm 包 | `@vectorize-io/hindsight-control-plane@1.2.0` |
| 上游提交 | `34a138684a899b6ec34175f7a2b633860466d289` |
| SHA-512 integrity | `sha512-4y3Z68M3qSeWxNvCjVcaZpO4TQ1gOYHpu47mRYvHw1kyYgwFZ8QL2VXRBK2RfAspWaYJzI9uC1h7lv47W6q1hA==` |
| 本仓维护源码 | [安装脚本](Install-HindsightUI.ps1)、[PowerShell 启动脚本](Start-HindsightUI.ps1)、[双击入口](Start-HindsightUI.cmd) |
| 官方部署说明 | [Standalone Control Plane](https://hindsight.vectorize.io/developer/installation#control-plane)、[Control Plane 配置](https://hindsight.vectorize.io/developer/configuration#control-plane) |

升级前核对原 API 版本和官方兼容性，记录新包来源与完整性；不自动跟随 `latest`。移除本管理端前先在原窗口 Ctrl+C，之后只处理 `control-plane` 目录及这两个 UI 启动脚本，不删除 `config\shared.env`、数据库或现有后端脚本。

## 目标宿主最小检查记录

2026-11-05，本次 Win 安装采用相同的官方 `npm pack`、解压和固定完整性校验路径。已通过 UI 浏览已有 bank、配置概览及既有失败操作。安装时观测该 bank 的记忆数和文档数均为 0，原写入提取失败；该计数只是此次安装检查的快照。UI 安装检查未新增记忆或文档，也未触发模型测试。页面检查未见 Sharp 错误，没有额外补装依赖。

Ctrl+C 后 UI 监听为零，原 Hindsight API、PostgreSQL、BGE 进程保留，后端健康检查通过。官方包的 SHA-512 匹配固定值，三个部署脚本的 SHA-256 与本仓源码一致，两个 PowerShell 脚本通过 Win 原生语法解析。证据保存在 Win `control-plane\install-receipt.json`。此记录不代表其他客户端或云端采用已验收。
