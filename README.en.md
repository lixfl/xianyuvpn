# xianyuvpn

> Lightweight command-line proxy tool based on Mihomo (Clash Meta) core, designed for headless Linux servers.
> Auto subscription update, hot config reload, RESTful API control, fake-ip DNS, SNI sniffing.

[中文](README.md) | English

## Features

- Built-in Web UI for visual management (dashboard, node switch, real-time logs)
- Pure CLI, no GUI dependencies
- One-click install to current directory (no root needed)
- Unified `xy` command for all operations
- Subscription auto-update with config hot reload
- Mixed proxy port (HTTP + SOCKS5)
- RESTful API for node switching and traffic monitoring
- fake-ip DNS + SNI sniffing
- Supports amd64 / arm64 / armv7

## Quick Start

### Install

```bash
git clone https://github.com/lixfl/xianyuvpn.git
cd xianyuvpn
chmod +x xy scripts/*.sh
./install.sh
```

### (Optional) Install `xy` global command

```bash
sudo ln -sf $(pwd)/xy /usr/local/bin/xy
```

Then you can use `xy` from anywhere:

```bash
xy update "https://your-subscription-url"
xy start
eval $(xy proxy)
```

### Usage without `xy`

```bash
# 1. Update subscription
./scripts/update-sub.sh "https://your-subscription-url"

# 2. Start proxy
./scripts/xianyuvpn.sh start

# 3. Enable proxy for current shell
eval $(./scripts/xianyuvpn.sh proxy)

# 4. Verify
curl https://api.ipify.org
```

## Commands

### With `xy` (recommended)

```bash
xy start        # Start proxy
xy stop         # Stop proxy
xy restart      # Restart proxy
xy status       # Show status
xy reload       # Hot reload config
xy test         # Test config file
xy log          # Follow logs
xy proxy        # Print proxy env vars
xy update [url] # Update subscription
xy install      # Download core and geo data
xy webui        # Start web interface (port 9091)
```

### Proxy Management

```bash
./scripts/xianyuvpn.sh start      # Start proxy
./scripts/xianyuvpn.sh stop       # Stop proxy
./scripts/xianyuvpn.sh restart    # Restart proxy
./scripts/xianyuvpn.sh status     # Show status
./scripts/xianyuvpn.sh reload     # Hot reload config
./scripts/xianyuvpn.sh test       # Test config file
./scripts/xianyuvpn.sh log        # Follow logs
./scripts/xianyuvpn.sh proxy      # Print proxy env vars
```

### Subscription Management

```bash
# Update with URL directly
./scripts/update-sub.sh "https://your-subscription-url"

# Save URL for later use
echo "https://your-subscription-url" > config/sub.txt
./scripts/update-sub.sh

# Update and reload
./scripts/update-sub.sh && ./scripts/xianyuvpn.sh reload
```

### systemd Service (Optional)

```bash
sudo cp systemd/xianyuvpn.service /etc/systemd/system/
sudo sed -i "s|/opt/xianyuvpn|$(pwd)|g" /etc/systemd/system/xianyuvpn.service
sudo systemctl daemon-reload
sudo systemctl enable --now xianyuvpn
sudo systemctl status xianyuvpn
sudo journalctl -u xianyuvpn -f
```

### Web UI (Optional)

Start the built-in web interface:

```bash
xy webui
# or specify port
xy webui --port 8080
```

Then open `http://your-server-ip:9091` in your browser.

Default credentials: `admin` / `admin123` (please change after first login)

WebUI Features (Finance SaaS style dashboard):

- **Dashboard**: running status, current node, real-time up/down speed, uptime, total traffic, active connections, run mode, traffic trend area chart, KPI cards with sparklines
- **Node Management**: proxy group tabs, node search, delay sorting, batch speed test, click to switch
- **Connection Management**: active connections list, close single connection, close all
- **Rule Management**: full rule list with search filter
- **Logs**: real-time streaming, level filter (info/warning/error/debug), auto-scroll, clear
- **Settings**: start/stop/restart/reload, run mode switch (rule/global/direct), log level, allow LAN, subscription update, base config editor, system info
- **Account**: change password, public access toggle

### API Operations

```bash
# Core version
curl -s http://127.0.0.1:9090/version

# List all nodes
curl -s http://127.0.0.1:9090/proxies | python3 -m json.tool

# Current selected node
curl -s http://127.0.0.1:9090/proxies/GLOBAL | python3 -c "import sys,json;print(json.load(sys.stdin).get('now'))"

# Switch node
curl -X PUT http://127.0.0.1:9090/proxies/GLOBAL -d '{"name":"NodeName"}'

# Node speed test
curl -s "http://127.0.0.1:9090/proxies/NodeName/delay?timeout=5000&url=http://www.gstatic.com/generate_204"

# Real-time traffic
curl -s http://127.0.0.1:9090/traffic
```

## Ports

| Port | Purpose |
|------|---------|
| 7890 | Mixed proxy (HTTP + SOCKS5) |
| 9090 | RESTful API controller |
| 9091 | Web UI interface |
| 1053 | DNS (fake-ip mode) |

## Directory Structure

```
xianyuvpn/
├── xy                    # Unified CLI entry (link to /usr/local/bin for global use)
├── install.sh            # One-click installer (download core + geo data)
├── README.md             # Chinese documentation
├── README.en.md          # English documentation
├── LICENSE
├── scripts/              # Management scripts
│   ├── update-sub.sh     # Subscription update
│   ├── xianyuvpn.sh      # Proxy management
│   └── webui.py          # Web UI server
├── config/
│   ├── base.yaml         # Base config template
│   ├── config.yaml       # Generated runtime config (auto-generated)
│   └── sub.txt           # Saved subscription URL (optional)
├── bin/
│   └── mihomo            # Mihomo core (downloaded by install.sh)
├── data/                 # GeoIP/GeoSite data (downloaded by install.sh)
├── systemd/
│   ├── xianyuvpn.service      # Proxy systemd service
│   └── xianyuvpn-webui.service # WebUI systemd service
├── mihomo.log            # Runtime log
└── mihomo.pid            # Process PID file
```

## Configuration

Base config is in `config/base.yaml`:

- `mixed-port`: proxy port, default 7890
- `allow-lan`: allow LAN connections, default false
- `external-controller`: API listen address, default 127.0.0.1:9090
- `mode`: rule / global / direct, default rule
- `dns`: fake-ip DNS config
- `sniffer`: SNI sniffing config

After editing, run `./scripts/update-sub.sh` to regenerate config, then `./scripts/xianyuvpn.sh reload`.

## FAQ

**Q: Subscription returns node links instead of Clash config?**
A: Add `?clash=1` or `&flag=clash` to your subscription URL.

**Q: How to share proxy with other devices on LAN?**
A: Set `allow-lan: true` in `config/base.yaml`, regenerate config and restart. Other devices use `http://your-server-ip:7890`.

**Q: Dependencies?**
A: Python 3 + PyYAML (for config merging), curl, gzip. On Ubuntu/Debian: `pip3 install pyyaml`.

## Tech Stack

- [Mihomo](https://github.com/MetaCubeX/mihomo) - Clash Meta core (GPL-3.0)
- [meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat) - GeoIP/GeoSite data

## License

Scripts in this project are under MIT License. Mihomo core and geo data follow their respective licenses.

## Disclaimer

For learning and communication purposes only. Please comply with local laws and regulations.
