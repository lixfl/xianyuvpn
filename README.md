# xianyuvpn

> 基于 Mihomo (Clash Meta) 核心的轻量级命令行代理工具，专为无图形界面的 Linux 服务器设计。
> 支持订阅自动更新、配置热重载、RESTful API 控制、fake-ip DNS、SNI 嗅探。

[English](README.en.md) | 中文

## 功能特性

- 内置 Web UI 可视化管理（仪表盘、节点切换、实时日志）
- 纯命令行，无 GUI 依赖
- 一键安装到当前目录（无需 root）
- 统一 `xy` 命令管理所有操作
- 订阅自动更新 + 配置热重载
- 混合代理端口（HTTP + SOCKS5）
- RESTful API 支持节点切换和流量监控
- fake-ip DNS + SNI 嗅探
- 支持 amd64 / arm64 / armv7 架构

## 快速开始

### 安装

```bash
git clone https://github.com/lixfl/xianyuvpn.git
cd xianyuvpn
chmod +x xy scripts/*.sh
./install.sh
```

### （可选）安装全局 `xy` 命令

```bash
sudo ln -sf $(pwd)/xy /usr/local/bin/xy
```

之后即可在任意目录使用 `xy`：

```bash
xy update "https://你的订阅链接"
xy start
eval $(xy proxy)
```

### 不使用 `xy` 的方式

```bash
# 1. 更新订阅
./scripts/update-sub.sh "https://你的订阅链接"

# 2. 启动代理
./scripts/xianyuvpn.sh start

# 3. 当前终端启用代理
eval $(./scripts/xianyuvpn.sh proxy)

# 4. 验证
curl https://api.ipify.org
```

## 命令说明

### 使用 `xy`（推荐）

```bash
xy start        # 启动代理
xy stop         # 停止代理
xy restart      # 重启代理
xy status       # 查看状态
xy reload       # 热重载配置
xy test         # 测试配置文件
xy log          # 实时日志
xy proxy        # 输出代理环境变量
xy update [url] # 更新订阅
xy install      # 下载核心和 geo 数据
xy webui        # 启动 Web 界面（端口 9091）
```

### 代理管理

```bash
./scripts/xianyuvpn.sh start      # 启动代理
./scripts/xianyuvpn.sh stop       # 停止代理
./scripts/xianyuvpn.sh restart    # 重启代理
./scripts/xianyuvpn.sh status     # 查看状态
./scripts/xianyuvpn.sh reload     # 热重载配置
./scripts/xianyuvpn.sh test       # 测试配置文件
./scripts/xianyuvpn.sh log        # 实时日志
./scripts/xianyuvpn.sh proxy      # 输出代理环境变量
```

### 订阅管理

```bash
# 直接用链接更新
./scripts/update-sub.sh "https://你的订阅链接"

# 保存链接以便后续使用
echo "https://你的订阅链接" > config/sub.txt
./scripts/update-sub.sh

# 更新并重载
./scripts/update-sub.sh && ./scripts/xianyuvpn.sh reload
```

### systemd 服务（可选）

```bash
sudo cp systemd/xianyuvpn.service /etc/systemd/system/
sudo sed -i "s|/opt/xianyuvpn|$(pwd)|g" /etc/systemd/system/xianyuvpn.service
sudo systemctl daemon-reload
sudo systemctl enable --now xianyuvpn
sudo systemctl status xianyuvpn
sudo journalctl -u xianyuvpn -f
```

### Web UI（可选）

启动内置 Web 界面：

```bash
xy webui
# 或指定端口
xy webui --port 8080
```

然后在浏览器打开 `http://你的服务器IP:9091`。

默认账号：`admin` / `admin123`（首次登录后请修改密码）

WebUI 功能（Finance SaaS 风格仪表盘）：

- **仪表盘**：运行状态、当前节点、实时上下行速度、运行时间、总流量、活跃连接、运行模式、流量趋势面积图、KPI 卡片带迷你趋势线
- **节点管理**：代理分组标签、节点搜索、延迟排序、批量测速、点击切换
- **连接管理**：活跃连接列表、关闭单个连接、关闭全部连接
- **规则管理**：完整规则列表 + 搜索过滤
- **日志**：实时流式输出、级别过滤（info/warning/error/debug）、自动滚动、清空
- **设置**：启动/停止/重启/热重载、运行模式切换（rule/global/direct）、日志级别、允许局域网、订阅更新、基础配置编辑器、系统信息
- **账号**：修改密码、公网访问开关

### API 操作

```bash
# 核心版本
curl -s http://127.0.0.1:9090/version

# 列出所有节点
curl -s http://127.0.0.1:9090/proxies | python3 -m json.tool

# 当前选中节点
curl -s http://127.0.0.1:9090/proxies/GLOBAL | python3 -c "import sys,json;print(json.load(sys.stdin).get('now'))"

# 切换节点
curl -X PUT http://127.0.0.1:9090/proxies/GLOBAL -d '{"name":"节点名称"}'

# 节点测速
curl -s "http://127.0.0.1:9090/proxies/节点名称/delay?timeout=5000&url=http://www.gstatic.com/generate_204"

# 实时流量
curl -s http://127.0.0.1:9090/traffic
```

## 端口说明

| 端口 | 用途 |
|------|------|
| 7890 | 混合代理（HTTP + SOCKS5） |
| 9090 | RESTful API 控制端口 |
| 9091 | Web UI 界面 |
| 1053 | DNS（fake-ip 模式） |

## 目录结构

```
xianyuvpn/
├── xy                    # 统一 CLI 入口（可软链接到 /usr/local/bin 全局使用）
├── install.sh            # 一键安装脚本（下载核心 + geo 数据）
├── README.md             # 中文说明文档
├── README.en.md          # 英文说明文档
├── LICENSE
├── scripts/              # 管理脚本
│   ├── update-sub.sh     # 订阅更新
│   ├── xianyuvpn.sh      # 代理管理
│   └── webui.py          # Web UI 服务
├── config/
│   ├── base.yaml         # 基础配置模板
│   ├── config.yaml       # 运行时配置（自动生成）
│   └── sub.txt           # 保存的订阅链接（可选）
├── bin/
│   └── mihomo            # Mihomo 核心（install.sh 下载）
├── data/                 # GeoIP/GeoSite 数据（install.sh 下载）
├── systemd/
│   ├── xianyuvpn.service      # 代理 systemd 服务
│   └── xianyuvpn-webui.service # WebUI systemd 服务
├── mihomo.log            # 运行日志
└── mihomo.pid            # 进程 PID 文件
```

## 配置说明

基础配置在 `config/base.yaml`：

- `mixed-port`：代理端口，默认 7890
- `allow-lan`：允许局域网连接，默认 false
- `external-controller`：API 监听地址，默认 127.0.0.1:9090
- `mode`：rule / global / direct，默认 rule
- `dns`：fake-ip DNS 配置
- `sniffer`：SNI 嗅探配置

修改后运行 `./scripts/update-sub.sh` 重新生成配置，然后 `./scripts/xianyuvpn.sh reload` 热重载。

## 常见问题

**Q：订阅返回的是节点链接而不是 Clash 配置？**
A：在订阅链接后加上 `?clash=1` 或 `&flag=clash`。

**Q：如何让局域网其他设备使用代理？**
A：在 `config/base.yaml` 中设置 `allow-lan: true`，重新生成配置并重启。其他设备使用 `http://你的服务器IP:7890`。

**Q：依赖什么？**
A：Python 3 + PyYAML（用于配置合并）、curl、gzip。Ubuntu/Debian 上：`pip3 install pyyaml`。

## 技术栈

- [Mihomo](https://github.com/MetaCubeX/mihomo) - Clash Meta 核心（GPL-3.0）
- [meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat) - GeoIP/GeoSite 数据

## 许可证

本项目脚本采用 MIT 许可证。Mihomo 核心和 geo 数据遵循各自的许可证。

## 免责声明

仅供学习交流使用，请遵守当地法律法规。
