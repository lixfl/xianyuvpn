#!/usr/bin/env python3
"""
xianyuvpn WebUI - Complete web interface
Usage: python3 webui.py [--port 9091] [--host 0.0.0.0]
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote
import urllib.request
import urllib.error

# ============ Paths ============
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
MIHOMO_BIN = os.path.join(PROJECT_DIR, "bin", "mihomo")
CONFIG_DIR = os.path.join(PROJECT_DIR, "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.yaml")
BASE_FILE = os.path.join(CONFIG_DIR, "base.yaml")
DATA_DIR = os.path.join(PROJECT_DIR, "data")
PID_FILE = os.path.join(PROJECT_DIR, "mihomo.pid")
LOG_FILE = os.path.join(PROJECT_DIR, "mihomo.log")
SUB_FILE = os.path.join(CONFIG_DIR, "sub.txt")
UPDATE_SCRIPT = os.path.join(SCRIPT_DIR, "update-sub.sh")
MIHOMO_API = "http://127.0.0.1:9090"

WEB_HOST = "0.0.0.0"
WEB_PORT = 9091

def read_mihomo_secret():
    """Read external-controller secret from config so API calls stay authorized"""
    for f in (CONFIG_FILE, BASE_FILE):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                content = fh.read()
            m = re.search(r"^secret:\s*['\"]?([^'\"#\r\n]*?)['\"]?\s*$", content, flags=re.MULTILINE)
            if m:
                return m.group(1).strip()
        except OSError:
            continue
    return ""

def mihomo_headers():
    headers = {}
    secret = read_mihomo_secret()
    if secret:
        headers["Authorization"] = "Bearer " + secret
    return headers

# ============ Process Control ============
def api_alive():
    """Check if mihomo API is responding"""
    try:
        req = urllib.request.Request(MIHOMO_API + "/version")
        for k, v in mihomo_headers().items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except:
        return False

def find_mihomo_pid():
    """Locate mihomo PID: PID file first, then /proc scan (Linux)"""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return pid
        except (ValueError, OSError):
            pass
    # Fallback: scan /proc for a process running our mihomo binary
    try:
        bin_abs = os.path.realpath(MIHOMO_BIN) if os.path.exists(MIHOMO_BIN) else None
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % entry, "rb") as f:
                    argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
                if not argv:
                    continue
                exe = argv[0]
                if (bin_abs and os.path.realpath(exe) == bin_abs) or \
                   (os.path.basename(exe) == "mihomo" and "-f" in argv):
                    return int(entry)
            except (OSError, PermissionError, ValueError):
                continue
    except OSError:
        pass
    return None

def is_running():
    if find_mihomo_pid() is not None:
        return True
    # Fallback: mihomo may run elsewhere but expose the API on our port
    return api_alive()

def get_pid():
    return find_mihomo_pid()

def start_mihomo():
    if is_running():
        return True, "Already running"
    if not os.path.exists(MIHOMO_BIN):
        return False, "mihomo binary not found, run install.sh first"
    if not os.path.exists(CONFIG_FILE):
        return False, "config.yaml not found, run update-sub first"
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        log = open(LOG_FILE, "a")
        # Use stdbuf for line-buffered output so logs appear in real-time;
        # fall back to raw invocation if stdbuf is unavailable.
        cmd = [MIHOMO_BIN, "-d", DATA_DIR, "-f", CONFIG_FILE]
        stdbuf = shutil.which("stdbuf")
        if stdbuf:
            cmd = [stdbuf, "-oL", "-eL"] + cmd
        proc = subprocess.Popen(
            cmd,
            stdout=log, stderr=subprocess.STDOUT,
            cwd=PROJECT_DIR,
            start_new_session=True
        )
        log.close()
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
        # Wait for API to come up
        for _ in range(20):
            time.sleep(0.5)
            if proc.poll() is not None:
                return False, "mihomo exited unexpectedly, check logs"
            if api_alive():
                return True, "Started successfully"
        if proc.poll() is None:
            return True, "Started successfully"
        return False, "Failed to start, check logs"
    except Exception as e:
        return False, str(e)

def stop_mihomo():
    pid = find_mihomo_pid()
    if pid is None:
        # Nothing to kill; clean stale PID file if any
        if os.path.exists(PID_FILE):
            try: os.remove(PID_FILE)
            except OSError: pass
        return True, "Not running"
    try:
        os.kill(pid, 15)
        for _ in range(20):
            time.sleep(0.3)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            try:
                os.kill(pid, 9)
                time.sleep(0.5)
            except OSError:
                pass
        if os.path.exists(PID_FILE):
            try: os.remove(PID_FILE)
            except OSError: pass
        return True, "Stopped"
    except Exception as e:
        return False, str(e)

def restart_mihomo():
    stop_mihomo()
    time.sleep(1)
    return start_mihomo()

def mihomo_api(path, method="GET", data=None):
    url = MIHOMO_API + path
    try:
        req = urllib.request.Request(url, method=method)
        for k, v in mihomo_headers().items():
            req.add_header(k, v)
        if data is not None:
            req.data = json.dumps(data).encode()
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode(), resp.status
    except urllib.error.HTTPError as e:
        return e.read().decode(), e.code
    except Exception as e:
        return json.dumps({"error": str(e)}), 500

def process_uptime(pid):
    """Seconds since process start (Linux /proc based)"""
    try:
        with open("/proc/%d/stat" % pid) as f:
            stat = f.read()
        after_comm = stat.rsplit(")", 1)[1].split()
        starttime_ticks = int(after_comm[19])  # field 22: starttime
        clk_tck = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime") as f:
            boot_uptime = float(f.read().split()[0])
        return max(0, int(boot_uptime - starttime_ticks / clk_tck))
    except:
        return None

# ============ WebUI Auth & Session (learned from QQBot-Web-Adapter) ============
WEBUI_CONFIG_FILE = os.path.join(CONFIG_DIR, "webui.json")
SESSION_TTL = 24 * 3600          # Session TTL: 24h
SESSION_COOKIE = "xy_session"
DEVICE_COOKIE = "xy_device"
DEVICE_TTL = 365 * 24 * 3600     # Trusted device TTL: 1 year
LOGIN_FAIL_THRESHOLD = 5         # Device-level block after 5 failures
LOGIN_ATTEMPTS_TTL = 30 * 60     # Failure counter expires in 30min
CAPTCHA_TTL = 3 * 60             # Captcha valid for 3min
CAPTCHA_COOLDOWN = 60            # Captcha resend cooldown 60s
RATELIMIT_WINDOW = 60            # Rate limit window (seconds)
RATELIMIT_MAX = 500              # Max API requests per IP per window
RATELIMIT_LOGIN_MAX = 10         # Max login/captcha requests per IP per window
CAPTCHA_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
LOGIN_FREE_PATHS = {"/api/login", "/api/logout", "/api/login/captcha", "/api/login/status"}

_auth_lock = threading.Lock()
_sessions = {}        # token -> {"username": str, "expires": float}
_attempts = {}        # deviceKey -> {"count": int, "expires": float}
_blocked = {}         # deviceKey -> True (blocked until captcha login)
_captchas = {}        # ip -> {"code": str, "expires": float}
_captcha_cd = {}      # ip -> cooldown expiry timestamp
_rate = {}            # ip -> {"count": int, "login": int, "reset": float}

WEBUI_CONFIG = None   # Loaded at startup

def _default_webui_config():
    return {
        "login": {"enabled": True, "username": "admin", "password": ""},
        "publicAccess": True,
        "trustedDevices": {},
    }

def hash_password(password, salt=None):
    """PBKDF2-HMAC-SHA256 salted hash, stored as 'salt$hex'"""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return salt + "$" + dk.hex()

def verify_password(password, stored):
    try:
        salt, _, hexdigest = stored.partition("$")
        if not salt or not hexdigest:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
        return hmac.compare_digest(dk.hex(), hexdigest)
    except Exception:
        return False

def load_webui_config():
    cfg = _default_webui_config()
    try:
        with open(WEBUI_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if isinstance(data.get("login"), dict):
                cfg["login"].update(data["login"])
            if "publicAccess" in data:
                cfg["publicAccess"] = bool(data["publicAccess"])
            if isinstance(data.get("trustedDevices"), dict):
                cfg["trustedDevices"] = data["trustedDevices"]
    except (OSError, ValueError):
        pass
    if not cfg["login"].get("password"):
        # First run: default password admin123 (change it after first login)
        cfg["login"]["password"] = hash_password("admin123")
    return cfg

def save_webui_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = WEBUI_CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, WEBUI_CONFIG_FILE)
    except OSError:
        pass

def gen_token():
    return secrets.token_hex(32)

def gen_captcha():
    return "".join(secrets.choice(CAPTCHA_CHARS) for _ in range(32))

def get_client_ip(handler):
    xff = handler.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    xri = handler.headers.get("X-Real-IP")
    if xri:
        return xri.strip()
    try:
        return handler.client_address[0]
    except Exception:
        return "127.0.0.1"

def is_private_ip(ip):
    """Loopback / private network check (IPv4 + IPv6)"""
    if not ip:
        return True
    s = ip[7:] if ip.startswith("::ffff:") else ip
    if s in ("127.0.0.1", "localhost", "::1"):
        return True
    if s.startswith("10.") or s.startswith("192.168."):
        return True
    if re.match(r"^172\.(1[6-9]|2\d|3[01])\.", s):
        return True
    if re.match(r"^f[cde][0-9a-f]{0,2}:", s, re.IGNORECASE):
        return True
    return False

def get_cookie(handler, name):
    cookie = handler.headers.get("Cookie", "") or ""
    m = re.search(name + r"=([^;]+)", cookie)
    return m.group(1) if m else None

def _purge_expired_sessions_locked():
    now = time.time()
    for tok in [t for t, s in _sessions.items() if s["expires"] < now]:
        _sessions.pop(tok, None)

def set_session(token, username):
    with _auth_lock:
        _purge_expired_sessions_locked()
        _sessions[token] = {"username": username, "expires": time.time() + SESSION_TTL}

def get_session(handler):
    token = get_cookie(handler, SESSION_COOKIE)
    if not token:
        return None
    with _auth_lock:
        s = _sessions.get(token)
        if not s:
            return None
        if s["expires"] < time.time():
            _sessions.pop(token, None)
            return None
        return {"username": s["username"], "token": token}

def delete_session(token):
    with _auth_lock:
        _sessions.pop(token, None)

def purge_all_sessions():
    with _auth_lock:
        _sessions.clear()

def rate_limit_ok(ip, is_login_path):
    now = time.time()
    with _auth_lock:
        e = _rate.get(ip)
        if not e or now > e["reset"]:
            e = {"count": 0, "login": 0, "reset": now + RATELIMIT_WINDOW}
            _rate[ip] = e
        e["count"] += 1
        if is_login_path:
            e["login"] += 1
        if e["count"] > RATELIMIT_MAX:
            return False
        if is_login_path and e["login"] > RATELIMIT_LOGIN_MAX:
            return False
        return True

def _cleanup_loop():
    """Periodically purge expired in-memory entries"""
    while True:
        time.sleep(60)
        now = time.time()
        with _auth_lock:
            for k in [k for k, v in _rate.items() if now > v["reset"]]:
                _rate.pop(k, None)
            for k in [k for k, v in _attempts.items() if now > v["expires"]]:
                _attempts.pop(k, None)
            for k in [k for k, v in _captchas.items() if now > v["expires"]]:
                _captchas.pop(k, None)
            for k in [k for k, v in _captcha_cd.items() if now > v]:
                _captcha_cd.pop(k, None)
            _purge_expired_sessions_locked()

# ============ Web UI HTML ============
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>xianyuvpn WebUI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Quicksand:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
/* ============ Funina Light Theme ============ */
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --fu-blue:#2563eb;
  --fu-blue-soft:#eff6ff;
  --fu-blue-hover:#1d4ed8;
  --fu-ink:#0f172a;
  --fu-muted:#64748b;
  --fu-line:#e2e8f0;
  --fu-page:#f5f8fc;
  --fu-white:#ffffff;
  --fu-radius:6px;
  --fu-nav-w:240px;
  --fu-success:#16a34a;
  --fu-success-soft:#f0fdf4;
  --fu-danger:#dc2626;
  --fu-danger-soft:#fef2f2;
  --fu-warning:#d97706;
  --fu-warning-soft:#fffbeb;
  /* Legacy variable aliases (for JS inline styles) */
  --text-secondary:var(--fu-muted);
  --text-tertiary:#94a3b8;
  --heroui-default-400:#94a3b8;
}
html,body{height:100%}
body{
  font-family:"PingFang SC","HarmonyOS Sans","Segoe UI","Microsoft YaHei",sans-serif;
  background:var(--fu-page);
  color:var(--fu-ink);
  min-height:100vh;
  -webkit-font-smoothing:antialiased;
}
h1,h2,h3,h4,h5,h6{color:var(--fu-ink);font-weight:600}
.app{display:flex;min-height:100vh}
/* ============ Sidebar ============ */
.sidebar{
  width:var(--fu-nav-w);
  background:var(--fu-white);
  border-right:1px solid var(--fu-line);
  padding:0;
  flex-shrink:0;
  display:flex;
  flex-direction:column;
  position:fixed;
  top:0;left:0;bottom:0;
  z-index:100;
  overflow-y:auto;
}
.logo{
  padding:20px 24px;
  font-size:18px;
  font-weight:700;
  color:var(--fu-ink);
  display:flex;
  align-items:center;
  gap:10px;
  border-bottom:1px solid var(--fu-line);
  letter-spacing:-0.01em;
  position:relative;
}
.logo::before{
  content:'';
  width:28px;height:28px;
  background:url('data:image/png;base64,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') center/contain no-repeat;
  border-radius:6px;
  flex-shrink:0;
  position:relative;
}

.nav-item{
  padding:10px 24px;
  margin:2px 12px;
  border-radius:var(--fu-radius);
  cursor:pointer;
  color:var(--fu-muted);
  transition:all .15s ease;
  display:flex;
  align-items:center;
  gap:10px;
  font-size:14px;
  font-weight:500;
  user-select:none;
}
.nav-item:hover{
  background:var(--fu-blue-soft);
  color:var(--fu-blue);
}
.nav-item.active{
  background:var(--fu-blue);
  color:#fff;
  font-weight:600;
}
.nav-item.active::after{display:none}
.nav-item .icon{font-size:16px;width:18px;text-align:center;flex-shrink:0}
.nav-icon{width:20px;height:20px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0}.nav-icon svg{width:18px;height:18px}
/* ============ Main Content ============ */
.main{
  flex:1;
  margin-left:var(--fu-nav-w);
  padding:28px 32px;
  overflow-y:auto;
  max-width:100%;
  min-height:100vh;
}
.header{
  display:flex;
  justify-content:space-between;
  align-items:center;
  margin-bottom:24px;
  flex-wrap:wrap;
  gap:12px;
}
.header h1{font-size:24px;font-weight:700}
/* ============ Status Badge ============ */
.status-badge{
  padding:5px 14px;
  border-radius:var(--fu-radius);
  font-size:12px;
  font-weight:600;
  background:var(--fu-page);
  border:1px solid var(--fu-line);
  color:var(--fu-muted);
}
.status-badge.running{
  background:var(--fu-success-soft);
  color:var(--fu-success);
  border-color:#bbf7d0;
}
.status-badge.stopped{
  background:var(--fu-danger-soft);
  color:var(--fu-danger);
  border-color:#fecaca;
}
/* ============ Switch ============ */
.switch{
  position:relative;
  width:40px;height:22px;
  background:#cbd5e1;
  border-radius:999px;
  cursor:pointer;
  transition:all .2s ease;
  flex-shrink:0;
}
.switch.on{background:var(--fu-blue)}
.switch::after{
  content:'';
  position:absolute;
  top:2px;left:2px;
  width:18px;height:18px;
  background:#fff;
  border-radius:50%;
  transition:all .2s ease;
  box-shadow:0 1px 3px rgba(0,0,0,.2);
}
.switch.on::after{transform:translateX(18px)}
/* ============ Cards ============ */
.cards{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:14px;
  margin-bottom:20px;
}
.card{
  background:var(--fu-white);
  border-radius:var(--fu-radius);
  padding:18px 20px;
  transition:all .15s ease;
  border:1px solid var(--fu-line);
}
.card:hover{
  border-color:var(--fu-blue);
  box-shadow:0 4px 12px rgba(37,99,235,.08);
}
.card.clickable{cursor:pointer}
.card.clickable:active{transform:scale(.99)}
.card-label{
  font-size:12px;
  color:var(--fu-muted);
  margin-bottom:8px;
  font-weight:500;
}
.card-value{font-size:24px;font-weight:700;color:var(--fu-ink)}
.card-value.small{font-size:13px;font-weight:500;line-height:1.6;color:var(--fu-ink)}
/* ============ Buttons ============ */
.btn{
  padding:8px 18px;
  border-radius:var(--fu-radius);
  border:1px solid var(--fu-line);
  background:var(--fu-white);
  color:var(--fu-ink);
  cursor:pointer;
  font-size:13px;
  font-weight:500;
  font-family:inherit;
  transition:all .15s ease;
  min-height:34px;
  display:inline-flex;
  align-items:center;
  gap:6px;
}
.btn:hover{
  border-color:var(--fu-blue);
  color:var(--fu-blue);
  background:var(--fu-blue-soft);
}
.btn:active{transform:scale(.98)}
.btn.primary{
  background:var(--fu-blue);
  border-color:var(--fu-blue);
  color:#fff;
}
.btn.primary:hover{
  background:var(--fu-blue-hover);
  border-color:var(--fu-blue-hover);
  color:#fff;
}
.btn.danger{
  background:var(--fu-white);
  border-color:#fecaca;
  color:var(--fu-danger);
}
.btn.danger:hover{
  background:var(--fu-danger-soft);
  border-color:var(--fu-danger);
  color:var(--fu-danger);
}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none}
.btn.sm{padding:4px 12px;font-size:12px;min-height:26px}
/* ============ Group Tabs ============ */
.group-tabs{
  display:flex;
  gap:4px;
  margin-bottom:16px;
  flex-wrap:wrap;
  padding:4px;
  background:var(--fu-white);
  border-radius:var(--fu-radius);
  border:1px solid var(--fu-line);
  width:fit-content;
}
.group-tab{
  padding:6px 16px;
  border-radius:4px;
  background:transparent;
  color:var(--fu-muted);
  cursor:pointer;
  font-size:13px;
  font-weight:500;
  transition:all .15s ease;
  user-select:none;
}
.group-tab.active{
  background:var(--fu-blue);
  color:#fff;
}
.group-tab:hover:not(.active){
  color:var(--fu-blue);
  background:var(--fu-blue-soft);
}
/* ============ Node Cards ============ */
.node-grid{
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
  gap:12px;
}
.node-card{
  background:var(--fu-white);
  border-radius:var(--fu-radius);
  padding:14px 16px;
  cursor:pointer;
  transition:all .15s ease;
  border:1px solid var(--fu-line);
}
.node-card:hover{
  border-color:var(--fu-blue);
  box-shadow:0 2px 8px rgba(37,99,235,.08);
}
.node-card:active{transform:scale(.99)}
.node-card.active{
  border-color:var(--fu-blue);
  background:var(--fu-blue-soft);
}
.node-card.active .node-name{color:var(--fu-blue)}
.node-name{
  font-size:13px;
  font-weight:600;
  margin-bottom:4px;
  word-break:break-all;
  line-height:1.4;
  color:var(--fu-ink);
}
.node-type{
  font-size:11px;
  color:var(--fu-muted);
  text-transform:uppercase;
  letter-spacing:.04em;
  font-weight:500;
}
.node-delay{font-size:12px;margin-top:8px;font-weight:600}
.node-delay.fast{color:var(--fu-success)}
.node-delay.medium{color:var(--fu-warning)}
.node-delay.slow{color:var(--fu-danger)}
/* ============ Log Box ============ */
.log-box{
  background:#0f172a;
  border-radius:var(--fu-radius);
  padding:14px 16px;
  height:500px;
  overflow-y:auto;
  font-family:'JetBrains Mono','ui-monospace','SFMono-Regular','Menlo','Consolas',monospace;
  font-size:12px;
  line-height:1.8;
  border:1px solid #1e293b;
}
.log-line{margin-bottom:1px;padding:1px 0}
.log-line.info{color:#60a5fa}
.log-line.warning{color:#fbbf24}
.log-line.error{color:#f87171}
.log-line.debug{color:#94a3b8}
/* ============ Pages ============ */
.page{display:none}
.page.active{display:block;animation:fadeIn .2s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
/* ============ Traffic Chart ============ */
.traffic-chart{
  height:120px;
  background:var(--fu-white);
  border-radius:var(--fu-radius);
  margin-top:12px;
  position:relative;
  overflow:hidden;
  border:1px solid var(--fu-line);
}
.traffic-bar{
  position:absolute;
  bottom:0;
  width:4px;
  background:linear-gradient(to top,var(--fu-blue),#60a5fa);
  border-radius:2px 2px 0 0;
  transition:height .3s ease;
  opacity:.85;
}
/* ============ Finance SaaS Dashboard ============ */
.fin-hero{
  background:linear-gradient(135deg,var(--fu-blue) 0%,#1d4ed8 100%);
  border-radius:10px;
  padding:24px 28px;
  margin-bottom:16px;
  color:#fff;
  position:relative;
  overflow:hidden;
  transition:transform .15s,box-shadow .15s;
}
.fin-hero:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(37,99,235,.25)}
.fin-hero::after{
  content:'';
  position:absolute;
  top:-40px;right:-40px;
  width:160px;height:160px;
  background:rgba(255,255,255,.06);
  border-radius:50%;
}
.fin-hero-label{
  font-size:13px;
  opacity:.75;
  font-weight:500;
  margin-bottom:6px;
  text-transform:uppercase;
  letter-spacing:.05em;
}
.fin-hero-value{
  font-size:28px;
  font-weight:700;
  margin-bottom:10px;
  word-break:break-all;
  line-height:1.3;
}
.fin-hero-meta{
  display:flex;
  align-items:center;
  gap:8px;
  font-size:13px;
  opacity:.9;
}
.fin-hero-badge{
  background:rgba(255,255,255,.2);
  padding:3px 10px;
  border-radius:4px;
  font-weight:600;
  text-transform:uppercase;
  font-size:11px;
  letter-spacing:.04em;
}
.fin-hero-sep{opacity:.5}
.fin-hero-status{font-weight:500}
.fin-kpi-row{
  display:grid;
  grid-template-columns:repeat(4,1fr);
  gap:14px;
  margin-bottom:16px;
}
.fin-kpi-card{
  background:var(--fu-white);
  border:1px solid var(--fu-line);
  border-radius:10px;
  padding:18px 20px;
  transition:all .15s;
  position:relative;
  overflow:hidden;
}
.fin-kpi-card:hover{
  border-color:var(--fu-blue);
  box-shadow:0 4px 16px rgba(37,99,235,.08);
  transform:translateY(-1px);
}
.fin-kpi-label{
  font-size:12px;
  color:var(--fu-muted);
  font-weight:600;
  margin-bottom:8px;
  text-transform:uppercase;
  letter-spacing:.04em;
}
.fin-kpi-value{
  font-size:24px;
  font-weight:700;
  color:var(--fu-ink);
  font-variant-numeric:tabular-nums;
  margin-bottom:10px;
  line-height:1.2;
}
.fin-kpi-value.down{color:var(--fu-blue)}
.fin-kpi-value.up{color:var(--fu-success)}
.fin-kpi-spark{
  height:32px;
  display:flex;
  align-items:flex-end;
  gap:2px;
}
.fin-kpi-spark svg{width:100%;height:100%}
.fin-chart-card{
  background:var(--fu-white);
  border:1px solid var(--fu-line);
  border-radius:10px;
  padding:20px 24px;
  margin-bottom:16px;
}
.fin-chart-header{
  display:flex;
  justify-content:space-between;
  align-items:center;
  margin-bottom:16px;
}
.fin-chart-title{
  font-size:15px;
  font-weight:600;
  color:var(--fu-ink);
}
.fin-chart-legend{
  display:flex;
  gap:16px;
  font-size:12px;
  color:var(--fu-muted);
}
.fin-legend-item{display:flex;align-items:center;gap:6px}
.fin-legend-dot{
  width:8px;height:8px;
  border-radius:50%;
  display:inline-block;
}
.fin-legend-dot.down{background:var(--fu-blue)}
.fin-legend-dot.up{background:var(--fu-success)}
.fin-chart-area{
  height:180px;
  position:relative;
}
.fin-chart-area svg{width:100%;height:100%}
.fin-stats-row{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:14px;
}
.fin-stat-item{
  background:var(--fu-white);
  border:1px solid var(--fu-line);
  border-radius:10px;
  padding:16px 20px;
  display:flex;
  justify-content:space-between;
  align-items:center;
}
.fin-stat-label{
  font-size:13px;
  color:var(--fu-muted);
  font-weight:500;
}
.fin-stat-value{
  font-size:16px;
  font-weight:600;
  color:var(--fu-ink);
  font-variant-numeric:tabular-nums;
}
@media(max-width:768px){
  .fin-kpi-row{grid-template-columns:repeat(2,1fr);gap:10px}
  .fin-hero{padding:18px 20px}
  .fin-hero-value{font-size:22px}
  .fin-stats-row{grid-template-columns:1fr}
  .fin-chart-area{height:140px}
  .node-grid{grid-template-columns:repeat(2,1fr)}
  .group-tabs{overflow-x:auto;flex-wrap:nowrap}
  .group-tab{white-space:nowrap}
}
@media(max-width:480px){
  .fin-kpi-row{grid-template-columns:1fr}
  .fin-hero-value{font-size:18px}
  .fin-chart-area{height:120px}
  .node-grid{grid-template-columns:1fr}
  .header h1{font-size:18px}
  .log-box{height:350px;font-size:11px}
  .conn-table{font-size:11px}
  .conn-table th,.conn-table td{padding:6px 8px}
  .btn{padding:7px 14px;font-size:12px}
  .input,select{font-size:13px}
}
/* ============ Inputs ============ */
.input{
  padding:9px 14px;
  border-radius:var(--fu-radius);
  border:1px solid var(--fu-line);
  background:var(--fu-white);
  color:var(--fu-ink);
  font-size:14px;
  font-weight:400;
  font-family:inherit;
  width:100%;
  transition:all .15s ease;
}
.input:focus{
  outline:none;
  border-color:var(--fu-blue);
  box-shadow:0 0 0 3px rgba(37,99,235,.1);
}
.input::placeholder{color:#94a3b8}
select{
  padding:9px 14px;
  border-radius:var(--fu-radius);
  border:1px solid var(--fu-line);
  background:var(--fu-white);
  color:var(--fu-ink);
  font-size:14px;
  font-family:inherit;
  cursor:pointer;
  transition:all .15s;
  min-height:36px;
}
select:focus{
  outline:none;
  border-color:var(--fu-blue);
  box-shadow:0 0 0 3px rgba(37,99,235,.1);
}
textarea{
  width:100%;
  min-height:320px;
  padding:12px 14px;
  border-radius:var(--fu-radius);
  border:1px solid var(--fu-line);
  background:var(--fu-white);
  color:var(--fu-ink);
  font-family:'JetBrains Mono','ui-monospace','SFMono-Regular','Menlo','Consolas',monospace;
  font-size:12px;
  line-height:1.7;
  resize:vertical;
  transition:all .15s;
}
textarea:focus{
  outline:none;
  border-color:var(--fu-blue);
  box-shadow:0 0 0 3px rgba(37,99,235,.1);
}
/* ============ Rows ============ */
.row{
  display:flex;
  gap:12px;
  align-items:center;
  margin-bottom:14px;
  flex-wrap:wrap;
}
.row label{
  font-size:14px;
  color:var(--fu-muted);
  min-width:100px;
  font-weight:500;
}
/* ============ Toast ============ */
.toast{
  position:fixed;
  top:24px;right:24px;
  padding:12px 20px;
  border-radius:var(--fu-radius);
  background:var(--fu-white);
  color:var(--fu-ink);
  z-index:1000;
  opacity:0;
  transition:all .2s ease;
  font-size:14px;
  font-weight:500;
  box-shadow:0 8px 24px rgba(0,0,0,.12);
  border:1px solid var(--fu-line);
  transform:translateY(-8px);
  max-width:360px;
}
.toast.show{opacity:1;transform:translateY(0)}
.toast.success{border-left:3px solid var(--fu-success)}
.toast.error{border-left:3px solid var(--fu-danger)}
/* ============ Tables ============ */
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{
  padding:10px 12px;
  text-align:left;
  border-bottom:1px solid var(--fu-line);
}
th{
  color:var(--fu-muted);
  font-weight:600;
  font-size:12px;
  background:var(--fu-page);
}
tr:hover{background:var(--fu-blue-soft)}
.conn-table{font-size:12px}
.conn-table td{word-break:break-all}
/* ============ Filter Bar ============ */
.filter-bar{
  display:flex;
  gap:10px;
  margin-bottom:16px;
  flex-wrap:wrap;
  align-items:center;
}
/* ============ Badges ============ */
.badge{
  display:inline-block;
  padding:2px 10px;
  border-radius:var(--fu-radius);
  font-size:11px;
  font-weight:600;
}
.badge.rule{background:var(--fu-blue-soft);color:var(--fu-blue)}
.badge.direct{background:var(--fu-success-soft);color:var(--fu-success)}
.badge.reject{background:var(--fu-danger-soft);color:var(--fu-danger)}
/* ============ Section Title ============ */
.section-title{
  font-size:16px;
  font-weight:600;
  margin:24px 0 12px;
  padding-bottom:10px;
  border-bottom:1px solid var(--fu-line);
}
/* ============ Scrollbar ============ */
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:#94a3b8}
::selection{background:rgba(37,99,235,.15);color:var(--fu-ink)}
/* ============ Responsive ============ */
@media(max-width:768px){
  .sidebar{width:60px;padding:0}
  .nav-item{justify-content:center;padding:12px 0;gap:0}
  .nav-item .nav-icon svg{width:18px;height:18px}
  .nav-item span:not(.nav-icon){display:none}
  .logo{font-size:0;padding:16px 0;justify-content:center}
  .logo::before{margin:0;width:28px;height:28px}
  .main{margin-left:60px;padding:14px}
  .header{margin-bottom:16px}
  .header h1{font-size:20px}
  .cards{grid-template-columns:repeat(2,1fr);gap:10px}
  .table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  .table-wrap table{min-width:500px}
}
@media(max-width:480px){
  .sidebar{width:52px}
  .nav-item{padding:10px 0}
  .nav-item .nav-icon svg{width:16px;height:16px}
  .main{margin-left:52px;padding:12px}
  .cards{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="app">
  <div class="sidebar">
    <div class="logo">xianyuvpn</div>
    <div class="nav-item active" data-page="dashboard"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 16v-5"/><path d="M12 16V8"/><path d="M17 16v-3"/></svg></span><span>仪表盘</span></div>
    <div class="nav-item" data-page="nodes"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg></span><span>节点</span></div>
    <div class="nav-item" data-page="connections"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></span><span>连接</span></div>
    <div class="nav-item" data-page="rules"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/></svg></span><span>规则</span></div>
    <div class="nav-item" data-page="logs"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg></span><span>日志</span></div>
    <div class="nav-item" data-page="settings"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></span><span>设置</span></div>
    <div class="nav-item" data-page="account"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></span><span>账号</span></div>
    <div class="nav-item" onclick="logout()"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg></span><span>退出登录</span></div>
  </div>
  <div class="main">
    <!-- Dashboard -->
    <div class="page active" id="page-dashboard">
      <div class="header">
        <h1>仪表盘</h1>
      </div>
      <!-- Core status hero -->
      <div class="fin-hero" onclick="goPage('nodes')" style="cursor:pointer">
        <div class="fin-hero-label">当前节点</div>
        <div class="fin-hero-value" id="currentNode">-</div>
        <div class="fin-hero-meta">
          <span class="fin-hero-badge" id="runMode">-</span>
          <span class="fin-hero-sep">·</span>
          <span class="fin-hero-status" id="heroStatus">未运行</span>
        </div>
      </div>

      <!-- KPI cards row -->
      <div class="fin-kpi-row">
        <div class="fin-kpi-card">
          <div class="fin-kpi-label">下载速度</div>
          <div class="fin-kpi-value down" id="dlSpeed">0 B/s</div>
          <div class="fin-kpi-spark" id="dlSpark"></div>
        </div>
        <div class="fin-kpi-card">
          <div class="fin-kpi-label">上传速度</div>
          <div class="fin-kpi-value up" id="ulSpeed">0 B/s</div>
          <div class="fin-kpi-spark" id="ulSpark"></div>
        </div>
        <div class="fin-kpi-card" onclick="goPage('connections')" style="cursor:pointer">
          <div class="fin-kpi-label">活跃连接</div>
          <div class="fin-kpi-value" id="connCount">0</div>
          <div class="fin-kpi-spark" id="connSpark"></div>
        </div>
        <div class="fin-kpi-card">
          <div class="fin-kpi-label">运行时间</div>
          <div class="fin-kpi-value" id="uptime">-</div>
          <div class="fin-kpi-spark" id="uptimeSpark"></div>
        </div>
      </div>

      <!-- Main traffic chart -->
      <div class="fin-chart-card">
        <div class="fin-chart-header">
          <div class="fin-chart-title">流量趋势</div>
          <div class="fin-chart-legend">
            <span class="fin-legend-item"><span class="fin-legend-dot down"></span>下载</span>
            <span class="fin-legend-item"><span class="fin-legend-dot up"></span>上传</span>
          </div>
        </div>
        <div class="fin-chart-area" id="trafficChart"></div>
      </div>

      <!-- Stats footer -->
      <div class="fin-stats-row">
        <div class="fin-stat-item">
          <span class="fin-stat-label">总下载</span>
          <span class="fin-stat-value" id="totalDl">0 B</span>
        </div>
        <div class="fin-stat-item">
          <span class="fin-stat-label">总上传</span>
          <span class="fin-stat-value" id="totalUl">0 B</span>
        </div>
      </div>
    </div>

    <!-- Nodes -->
    <div class="page" id="page-nodes">
      <div class="header">
        <h1>节点管理</h1>
        <div style="display:flex;gap:8px">
          <button class="btn" onclick="testCurrentGroup()">⚡ 当前组测速</button>
          <button class="btn" onclick="testAllNodes()">🔍 全部测速</button>
        </div>
      </div>
      <div class="filter-bar">
        <input class="input" id="nodeFilter" placeholder="搜索节点..." oninput="renderNodes()" style="max-width:300px">
        <select id="delaySort" onchange="renderNodes()">
          <option value="default">默认排序</option>
          <option value="asc">延迟从低到高</option>
          <option value="desc">延迟从高到低</option>
        </select>
      </div>
      <div class="group-tabs" id="groupTabs"></div>
      <div class="node-grid" id="nodeGrid"></div>
    </div>

    <!-- Connections -->
    <div class="page" id="page-connections">
      <div class="header">
        <h1>连接管理</h1>
        <div style="display:flex;gap:8px">
          <button class="btn" onclick="loadConnections()">🔄 刷新</button>
          <button class="btn danger" onclick="closeAllConnections()">🗑️ 清空全部</button>
        </div>
      </div>
      <div style="overflow-x:auto">
        <table class="conn-table">
          <thead><tr><th>网络</th><th>源地址</th><th>目标</th><th>代理链</th><th>规则</th><th>上传</th><th>下载</th><th>操作</th></tr></thead>
          <tbody id="connBody"></tbody>
        </table>
      </div>
    </div>

    <!-- Rules -->
    <div class="page" id="page-rules">
      <div class="header">
        <h1>规则列表</h1>
        <input class="input" id="ruleFilter" placeholder="搜索规则..." oninput="renderRules()" style="max-width:300px">
      </div>
      <div id="ruleCount" style="color:var(--text-secondary);margin-bottom:10px;font-size:13px"></div>
      <div style="overflow-x:auto;max-height:600px;overflow-y:auto">
        <table>
          <thead><tr><th style="width:60px">#</th><th>规则</th><th style="width:120px">类型</th><th style="width:150px">目标</th></tr></thead>
          <tbody id="ruleBody"></tbody>
        </table>
      </div>
    </div>

    <!-- Logs -->
    <div class="page" id="page-logs">
      <div class="header">
        <h1>运行日志</h1>
        <div style="display:flex;gap:8px;align-items:center">
          <select id="logLevelFilterSelect" onchange="setLogLevelFilter(this.value)">
            <option value="all">全部</option>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="error">Error</option>
            <option value="debug">Debug</option>
          </select>
          <label style="font-size:13px;color:var(--text-secondary)"><input type="checkbox" id="autoScroll" checked> 自动滚动</label>
          <button class="btn" onclick="document.getElementById('logBox').innerHTML=''">🗑️ 清空</button>
        </div>
      </div>
      <div class="log-box" id="logBox"></div>
    </div>

    <!-- Settings -->
    <div class="page" id="page-settings">
      <div class="header"><h1>设置</h1></div>

      <div class="section-title">代理控制</div>
      <div class="card" style="margin-bottom:16px">
        <div class="row">
          <button class="btn primary" onclick="startProxy()">启动</button>
          <button class="btn" onclick="restartProxy()">重启</button>
          <button class="btn danger" onclick="stopProxy()">停止</button>
          <button class="btn" onclick="reloadConfig()">热重载配置</button>
        </div>
      </div>

      <div class="section-title">运行设置</div>
      <div class="card" style="margin-bottom:16px">
        <div class="row">
          <label>运行模式</label>
          <select id="modeSelect" onchange="changeMode(this.value)">
            <option value="rule">Rule（规则）</option>
            <option value="global">Global（全局）</option>
            <option value="direct">Direct（直连）</option>
          </select>
        </div>
        <div class="row">
          <label>日志级别</label>
          <select id="logLevelSelect" onchange="changeLogLevel(this.value)">
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="error">Error</option>
            <option value="debug">Debug</option>
            <option value="silent">Silent</option>
          </select>
        </div>
        <div class="row">
          <label>允许局域网</label>
          <div class="switch" id="allowLanSwitch" onclick="toggleAllowLan()"></div>
          <span style="font-size:12px;color:var(--text-secondary)">开启后局域网设备可通过本机 IP:7890 使用代理</span>
        </div>
      </div>

      <div class="section-title">订阅管理</div>
      <div class="card" style="margin-bottom:16px">
        <div class="row">
          <input class="input" id="subUrl" placeholder="输入订阅链接" style="flex:1">
          <button class="btn primary" onclick="updateSub()">更新订阅</button>
        </div>
      </div>

      <div class="section-title">运行配置编辑</div>
      <div class="card" style="margin-bottom:16px">
        <div class="row">
          <button class="btn" onclick="loadBaseConfig()">📖 读取配置</button>
          <button class="btn primary" onclick="saveBaseConfig()">💾 保存并重载</button>
          <span style="font-size:12px;color:var(--heroui-default-400)">编辑当前运行的 config.yaml</span>
        </div>
        <textarea id="baseConfigEditor" placeholder="点击读取配置..."></textarea>
      </div>

      <div class="section-title">系统信息</div>
      <div class="card">
        <div style="font-size:13px;color:var(--text-secondary);line-height:2">
          内核版本：<span id="coreVersion">-</span><br>
          配置文件：config/config.yaml<br>
          API 地址：127.0.0.1:9090<br>
          WebUI 端口：<span id="webuiPort">9091</span><br>
          项目目录：<span id="projectDir">-</span>
        </div>
      </div>
    </div>

    <!-- Account -->
    <div class="page" id="page-account">
      <div class="header"><h1>账号设置</h1></div>

      <div class="section-title">登录凭据</div>
      <div class="card" style="margin-bottom:16px">
        <div class="row">
          <label>用户名</label>
          <input class="input" id="acctUsername" placeholder="登录用户名" style="max-width:300px">
        </div>
        <div class="row">
          <label>新密码</label>
          <input class="input" type="password" id="acctPassword" placeholder="留空表示不修改" style="max-width:300px">
        </div>
        <div class="row">
          <label>确认密码</label>
          <input class="input" type="password" id="acctPassword2" placeholder="再次输入新密码" style="max-width:300px">
        </div>
        <div class="row">
          <button class="btn primary" onclick="saveAccount()">保存凭据</button>
          <span style="font-size:12px;color:var(--text-secondary)">修改密码后所有设备需重新登录</span>
        </div>
      </div>

      <div class="section-title">访问控制</div>
      <div class="card" style="margin-bottom:16px">
        <div class="row">
          <label>公网访问</label>
          <div class="switch" id="publicAccessSwitch" onclick="togglePublicAccess()"></div>
          <span style="font-size:12px;color:var(--text-secondary)">关闭后仅允许内网 IP 访问控制台</span>
        </div>
        <div class="row">
          <label>登录开关</label>
          <div class="switch" id="loginEnabledSwitch" onclick="toggleLoginEnabled()"></div>
          <span style="font-size:12px;color:var(--text-secondary)">关闭后免登录（仅建议内网使用）</span>
        </div>
      </div>

      <div class="section-title">会话</div>
      <div class="card">
        <div class="row">
          <button class="btn danger" onclick="logout()">退出登录</button>
          <span style="font-size:12px;color:var(--text-secondary)">会话有效期 24 小时</span>
        </div>
      </div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
let currentGroup=null,nodeData=[],currentNodeMap={},trafficHistory=[],uploadHistory=[],connHistory=[],totalDown=0,totalUp=0,logLevelFilter='all';
const delayCache={};

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function setLogLevelFilter(v){logLevelFilter=v}

async function api(path,method='GET',data=null){
  const opts={method};
  if(data){opts.headers={'Content-Type':'application/json'};opts.body=JSON.stringify(data)}
  try{
    const r=await fetch(path,opts);
    if(r.status===401){location.replace('/login.html');return{}}
    return await r.json()
  }catch(e){return{error:e.message}}
}
function toast(msg,type='success'){
  const t=document.getElementById('toast');t.textContent=msg;t.className='toast show '+type;
  setTimeout(()=>t.className='toast',2500);
}
function fmtSpeed(b){
  if(b<1024)return b+' B/s';if(b<1048576)return(b/1024).toFixed(1)+' KB/s';return(b/1048576).toFixed(2)+' MB/s';
}
function fmtBytes(b){
  if(b<1024)return b+' B';if(b<1048576)return(b/1024).toFixed(1)+' KB';if(b<1073741824)return(b/1048576).toFixed(1)+' MB';return(b/1073741824).toFixed(2)+' GB';
}

// Page navigation helper (for clickable dashboard cards)
function goPage(page){
  const item=document.querySelector('.nav-item[data-page="'+page+'"]');
  if(item)item.click();
}
async function cycleMode(){
  const s=await api('/api/status');
  const cur=s.mode||'rule';
  const modes=['rule','global','direct'];
  const idx=modes.indexOf(cur);
  const next=modes[(idx+1)%modes.length];
  const r=await api('/api/configs','PATCH',{mode:next});
  if(r&&r.success===false){toast(r.message||'切换失败','error')}
  else toast('模式已切换为: '+next);
  refreshStatus();
}

// Navigation
document.querySelectorAll('.nav-item').forEach(item=>{
  if(!item.dataset.page)return;
  item.onclick=()=>{
    document.querySelectorAll('.nav-item').forEach(i=>i.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
    item.classList.add('active');
    document.getElementById('page-'+item.dataset.page).classList.add('active');
    if(item.dataset.page==='nodes')loadNodes();
    if(item.dataset.page==='connections')loadConnections();
    if(item.dataset.page==='rules')loadRules();
    if(item.dataset.page==='settings')loadSettings();
    if(item.dataset.page==='account')loadAccount();
  };
});

// Status
async function refreshStatus(){
  try{
    const s=await api('/api/status');
    const badge=document.getElementById('statusBadge');
    const sw=document.getElementById('proxySwitch');
    const hs=document.getElementById('heroStatus');
    if(s.running){
      if(badge){badge.textContent='运行中';badge.className='status-badge running';}
      if(sw)sw.classList.add('on');
      if(hs)hs.textContent='运行中';
    }else{
      if(badge){badge.textContent='已停止';badge.className='status-badge stopped';}
      if(sw)sw.classList.remove('on');
      if(hs)hs.textContent='已停止';
    }
    if(s.current_node)document.getElementById('currentNode').textContent=s.current_node;
    if(s.version)document.getElementById('coreVersion').textContent=s.version;
    if(s.mode&&s.mode!=='-')document.getElementById('runMode').textContent=s.mode;
    if(s.project_dir)document.getElementById('projectDir').textContent=s.project_dir;
    if(s.uptime!=null){
      const sec=Math.floor(s.uptime);
      document.getElementById('uptime').textContent=Math.floor(sec/3600)+'h '+Math.floor((sec%3600)/60)+'m '+(sec%60)+'s';
    }else if(!s.running){
      document.getElementById('uptime').textContent='-';
    }
    // totals + connection count come from the core connection API
    const c=await api('/api/connections');
    if(c&&Array.isArray(c.connections)){
      const cn=c.connections.length;
      document.getElementById('connCount').textContent=cn;
      connHistory.push(cn);if(connHistory.length>30)connHistory.shift();
      drawSpark('connSpark',connHistory,'#64748b');
      if(typeof c.downloadTotal==='number'){totalDown=c.downloadTotal;document.getElementById('totalDl').textContent=fmtBytes(totalDown)}
      if(typeof c.uploadTotal==='number'){totalUp=c.uploadTotal;document.getElementById('totalUl').textContent=fmtBytes(totalUp)}
    }
  }catch(e){}
}

// Proxy control
async function toggleProxy(){const s=await api('/api/status');if(s.running)await stopProxy();else await startProxy()}
async function startProxy(){const r=await api('/api/start','POST');toast(r.message,r.success?'success':'error');refreshStatus()}
async function stopProxy(){const r=await api('/api/stop','POST');toast(r.message,r.success?'success':'error');refreshStatus()}
async function restartProxy(){const r=await api('/api/restart','POST');toast(r.message,r.success?'success':'error');setTimeout(refreshStatus,2000)}
async function reloadConfig(){const r=await api('/api/reload','POST');toast(r.message,r.success?'success':'error');if(r.success)setTimeout(refreshStatus,1000)}

// Nodes
async function loadNodes(){
  const data=await api('/api/proxies');
  const tabs=document.getElementById('groupTabs');tabs.innerHTML='';
  const groups=data.groups||[];
  currentNodeMap={};
  groups.forEach(g=>currentNodeMap[g.name]=g.now);
  if(!currentGroup&&groups.length)currentGroup=groups[0].name;
  groups.forEach(g=>{
    const tab=document.createElement('div');
    tab.className='group-tab'+(g.name===currentGroup?' active':'');
    tab.textContent=g.name;
    tab.onclick=()=>{currentGroup=g.name;loadNodes()};
    tabs.appendChild(tab);
  });
  nodeData=groups.find(g=>g.name===currentGroup)?.all||[];
  renderNodes();
}
function renderNodes(){
  const grid=document.getElementById('nodeGrid');grid.innerHTML='';
  const filter=document.getElementById('nodeFilter').value.toLowerCase();
  const sort=document.getElementById('delaySort').value;
  let nodes=nodeData.filter(n=>n.name.toLowerCase().includes(filter));
  if(sort==='asc')nodes.sort((a,b)=>(delayCache[a.name]??99999)-(delayCache[b.name]??99999));
  if(sort==='desc')nodes.sort((a,b)=>(delayCache[b.name]??0)-(delayCache[a.name]??0));
  const now=currentNodeMap[currentGroup];
  if(!nodes.length){
    grid.innerHTML='<div style="color:var(--text-secondary);padding:20px">没有节点（先更新订阅或选择分组）</div>';
  }
  nodes.forEach(node=>{
    const card=document.createElement('div');
    card.className='node-card'+(node.name===now?' active':'');
    const delay=delayCache[node.name];
    let delayHtml='<span style="color:var(--text-tertiary)">未测试</span>';
    if(delay!=null){
      if(delay>0){
        const cls=delay<200?'fast':delay<500?'medium':'slow';
        delayHtml='<span class="node-delay '+cls+'">'+delay+' ms</span>';
      }else{
        delayHtml='<span class="node-delay slow">超时</span>';
      }
    }
    card.innerHTML='<div class="node-name">'+esc(node.name)+'</div><div class="node-type">'+esc(node.type||'')+'</div>'+delayHtml;
    card.onclick=()=>selectNode(currentGroup,node.name);
    grid.appendChild(card);
  });
}
async function selectNode(group,node){
  const r=await api('/api/proxies/'+encodeURIComponent(group)+'/select','POST',{name:node});
  if(r.success){toast('已切换: '+node);loadNodes();refreshStatus()}
  else toast(r.message||'切换失败','error');
}
async function testNode(name){
  try{
    const r=await api('/api/proxies/'+encodeURIComponent(name)+'/delay');
    if(r&&typeof r.delay==='number'){delayCache[name]=r.delay;return r.delay}
  }catch(e){}
  delayCache[name]=0;return 0;
}
async function runTestBatch(names){
  const BATCH=10;
  for(let i=0;i<names.length;i+=BATCH){
    await Promise.all(names.slice(i,i+BATCH).map(n=>testNode(n)));
    renderNodes();
  }
}
async function testCurrentGroup(){
  const filter=document.getElementById('nodeFilter').value.toLowerCase();
  const nodes=nodeData.filter(n=>n.name.toLowerCase().includes(filter));
  if(!nodes.length){toast('当前分组没有节点','error');return}
  toast('正在测速 '+nodes.length+' 个节点...');
  await runTestBatch(nodes.map(n=>n.name));
  toast('测速完成');
}
async function testAllNodes(){
  const groups=await api('/api/proxies');
  const names=[...new Set((groups.groups||[]).flatMap(g=>(g.all||[]).map(n=>n.name)))];
  if(!names.length){toast('没有可测速的节点','error');return}
  toast('正在测试所有节点（'+names.length+'）...');
  await runTestBatch(names);
  toast('全部测速完成');
}

// Connections
async function loadConnections(){
  const data=await api('/api/connections');
  const body=document.getElementById('connBody');body.innerHTML='';
  document.getElementById('connCount').textContent=data.connections?.length||0;
  (data.connections||[]).forEach(c=>{
    const md=c.metadata||{};
    const tr=document.createElement('tr');
    tr.innerHTML='<td>'+esc(md.type||'')+'</td>'+
      '<td>'+esc((md.sourceIP||'')+':'+(md.sourcePort||''))+'</td>'+
      '<td>'+esc((md.host||md.destinationIP||'')+':'+(md.destinationPort||''))+'</td>'+
      '<td>'+esc((c.chains||[]).join(' → '))+'</td>'+
      '<td><span class="badge '+(c.rule==='DIRECT'?'direct':c.rule==='REJECT'?'reject':'rule')+'">'+esc(c.rule||'')+'</span></td>'+
      '<td>'+fmtBytes(c.upload||0)+'</td>'+
      '<td>'+fmtBytes(c.download||0)+'</td>'+
      '<td></td>';
    const btn=document.createElement('button');
    btn.className='btn sm danger';btn.textContent='关闭';
    btn.onclick=()=>closeConn(c.id);
    tr.lastChild.appendChild(btn);
    body.appendChild(tr);
  });
}
async function closeConn(id){
  await api('/api/connections/'+id,'DELETE');
  toast('已关闭连接');loadConnections();
}
async function closeAllConnections(){
  await api('/api/connections','DELETE');
  toast('已清空所有连接');loadConnections();
}

// Rules
let allRules=[];
async function loadRules(){
  const data=await api('/api/rules');
  allRules=data.rules||[];
  document.getElementById('ruleCount').textContent='共 '+allRules.length+' 条规则';
  renderRules();
}
function renderRules(){
  const body=document.getElementById('ruleBody');body.innerHTML='';
  const filter=document.getElementById('ruleFilter').value.toLowerCase();
  const rules=allRules.filter(r=>((r.payload||'')+' '+(r.type||'')+' '+(r.proxy||'')).toLowerCase().includes(filter));
  document.getElementById('ruleCount').textContent='共 '+rules.length+' 条规则'+(rules.length!==allRules.length?'（过滤自 '+allRules.length+' 条）':'');
  rules.slice(0,500).forEach((r,i)=>{
    const tr=document.createElement('tr');
    const cls=r.proxy==='DIRECT'?'direct':r.proxy==='REJECT'?'reject':'rule';
    tr.innerHTML='<td>'+(i+1)+'</td><td>'+esc(r.payload||'')+'</td><td>'+esc(r.type||'')+'</td><td><span class="badge '+cls+'">'+esc(r.proxy||'')+'</span></td>';
    body.appendChild(tr);
  });
  if(rules.length>500){
    const tr=document.createElement('tr');
    tr.innerHTML='<td colspan="4" style="text-align:center;color:var(--text-secondary)">... 还有 '+(rules.length-500)+' 条，使用搜索过滤</td>';
    body.appendChild(tr);
  }
}

// Logs
function detectLevel(line){
  const m=line.match(/level=(\w+)/);
  if(m)return m[1];
  const low=line.toLowerCase();
  if(low.includes('error'))return 'error';
  if(low.includes('warning')||low.includes('warn'))return 'warning';
  if(low.includes('debug'))return 'debug';
  return 'info';
}
function appendLog(line){
  const level=detectLevel(line);
  if(logLevelFilter!=='all'&&level!==logLevelFilter)return;
  const box=document.getElementById('logBox');
  const div=document.createElement('div');
  div.className='log-line '+level;div.textContent=line;
  box.appendChild(div);
  while(box.childElementCount>800)box.removeChild(box.firstChild);
  if(document.getElementById('autoScroll').checked)box.scrollTop=box.scrollHeight;
}
async function logLoop(){
  try{
    const r=await fetch('/api/logs');
    if(r.status===401){location.replace('/login.html');return}
    const reader=r.body.getReader();const decoder=new TextDecoder();let buf='';
    while(true){
      const{done,value}=await reader.read();if(done)break;
      buf+=decoder.decode(value,{stream:true});const lines=buf.split('\n');buf=lines.pop();
      lines.forEach(line=>{if(line.trim())appendLog(line)});
    }
  }catch(e){}
  setTimeout(logLoop,1000);
}

// Settings
async function loadSettings(){
  const s=await api('/api/status');
  if(s.mode&&s.mode!=='-')document.getElementById('modeSelect').value=s.mode;
  if(s.log_level)document.getElementById('logLevelSelect').value=s.log_level;
  document.getElementById('allowLanSwitch').classList.toggle('on',!!s.allow_lan);
  document.getElementById('webuiPort').textContent=window.location.port||'9091';
}
async function changeMode(mode){
  const r=await api('/api/configs','PATCH',{mode});
  if(r&&r.success===false){toast(r.message||'切换失败','error')}
  else toast('模式已切换为: '+mode);
  refreshStatus();
}
async function changeLogLevel(level){
  const r=await api('/api/configs','PATCH',{'log-level':level});
  if(r&&r.success===false){toast(r.message||'切换失败','error')}
  else toast('日志级别已切换');
}
async function toggleAllowLan(){
  const sw=document.getElementById('allowLanSwitch');
  const willEnable=!sw.classList.contains('on');
  toast('正在应用设置...');
  const r=await api('/api/allow-lan','POST',{enabled:willEnable});
  toast(r.message,r.success?'success':'error');
  if(r.success){
    sw.classList.toggle('on',willEnable);
    setTimeout(refreshStatus,3000);
  }
}
async function updateSub(){
  const url=document.getElementById('subUrl').value.trim();
  toast(url?'正在更新订阅...':'正在使用已保存的订阅更新...');
  const r=await api('/api/update-sub','POST',{url});
  toast(r.message,r.success?'success':'error');
  if(r.success)setTimeout(()=>location.reload(),1500);
}
async function loadBaseConfig(){
  const r=await api('/api/base-config');
  document.getElementById('baseConfigEditor').value=r.content||'';
  toast('配置已读取');
}
async function saveBaseConfig(){
  const content=document.getElementById('baseConfigEditor').value;
  toast('正在保存并重载...');
  const r=await api('/api/base-config','POST',{content});
  toast(r.message,r.success?'success':'error');
  if(r.success)setTimeout(()=>{loadSettings();refreshStatus()},3000);
}

// Traffic
async function trafficLoop(){
  try{
    const r=await fetch('/api/traffic');
    if(r.status===401){location.replace('/login.html');return}
    const reader=r.body.getReader();const decoder=new TextDecoder();let buf='';
    while(true){
      const{done,value}=await reader.read();if(done)break;
      buf+=decoder.decode(value,{stream:true});const lines=buf.split('\n');buf=lines.pop();
      for(const line of lines){
        if(!line.trim())continue;
        try{
          const d=JSON.parse(line);
          document.getElementById('dlSpeed').textContent=fmtSpeed(d.down||0);
          document.getElementById('ulSpeed').textContent=fmtSpeed(d.up||0);
          trafficHistory.push(d.down||0);if(trafficHistory.length>60)trafficHistory.shift();
          uploadHistory.push(d.up||0);if(uploadHistory.length>60)uploadHistory.shift();
          drawTraffic();
          drawSpark('dlSpark',trafficHistory,'#2563eb');
          drawSpark('ulSpark',uploadHistory,'#16a34a');
        }catch(e){}
      }
    }
  }catch(e){}
  setTimeout(trafficLoop,1000);
}
function drawTraffic(){
  const chart=document.getElementById('trafficChart');
  if(!chart)return;
  const W=chart.clientWidth||600,H=180,pad=4;
  const all=[...trafficHistory,...uploadHistory];
  const max=Math.max(...all,1);
  const n=trafficHistory.length;
  if(n<2){chart.innerHTML='';return;}
  const stepX=(W-pad*2)/(n-1);
  function toPath(arr){
    let d='';
    arr.forEach((v,i)=>{
      const x=pad+i*stepX;
      const y=H-pad-(v/max)*(H-pad*2);
      d+=(i===0?'M':'L')+x.toFixed(1)+','+y.toFixed(1)+' ';
    });
    return d;
  }
  const dlLine=toPath(trafficHistory);
  const ulLine=toPath(uploadHistory);
  const dlArea=dlLine+`L${pad+(n-1)*stepX},${H-pad} L${pad},${H-pad} Z`;
  const ulArea=ulLine+`L${pad+(n-1)*stepX},${H-pad} L${pad},${H-pad} Z`;
  chart.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs>
      <linearGradient id="dlGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#2563eb" stop-opacity="0.25"/>
        <stop offset="100%" stop-color="#2563eb" stop-opacity="0"/>
      </linearGradient>
      <linearGradient id="ulGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#16a34a" stop-opacity="0.2"/>
        <stop offset="100%" stop-color="#16a34a" stop-opacity="0"/>
      </linearGradient>
    </defs>
    <path d="${dlArea}" fill="url(#dlGrad)"/>
    <path d="${ulArea}" fill="url(#ulGrad)"/>
    <path d="${ulLine}" fill="none" stroke="#16a34a" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
    <path d="${dlLine}" fill="none" stroke="#2563eb" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}
function drawSpark(elId,data,color){
  const el=document.getElementById(elId);
  if(!el||data.length<2)return;
  const W=el.clientWidth||100,H=32;
  const max=Math.max(...data,1);
  const stepX=W/(data.length-1);
  let d='';
  data.forEach((v,i)=>{
    const x=i*stepX;
    const y=H-(v/max)*H;
    d+=(i===0?'M':'L')+x.toFixed(1)+','+y.toFixed(1)+' ';
  });
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

// Account
async function logout(){
  try{await api('/api/logout','POST')}catch(e){}
  location.replace('/login.html');
}
async function loadAccount(){
  const r=await api('/api/account');
  if(!r)return;
  document.getElementById('acctUsername').value=r.username||'';
  document.getElementById('acctPassword').value='';
  document.getElementById('acctPassword2').value='';
  document.getElementById('publicAccessSwitch').classList.toggle('on',!!r.publicAccess);
  document.getElementById('loginEnabledSwitch').classList.toggle('on',r.loginEnabled!==false);
}
async function saveAccount(){
  const username=document.getElementById('acctUsername').value.trim();
  const pw=document.getElementById('acctPassword').value;
  const pw2=document.getElementById('acctPassword2').value;
  if(!username){toast('用户名不能为空','error');return}
  if(pw&&pw!==pw2){toast('两次输入的密码不一致','error');return}
  if(pw&&pw.length<6){toast('密码至少 6 位','error');return}
  const r=await api('/api/account','POST',{username,password:pw});
  if(r&&r.ok){toast(r.message||'已保存');loadAccount()}
  else toast((r&&r.error)||'保存失败','error');
}
async function togglePublicAccess(){
  const sw=document.getElementById('publicAccessSwitch');
  const willEnable=!sw.classList.contains('on');
  const r=await api('/api/account','POST',{publicAccess:willEnable});
  if(r&&r.ok){sw.classList.toggle('on',willEnable);toast(willEnable?'公网访问已开启':'公网访问已关闭')}
  else toast((r&&r.error)||'设置失败','error');
}
async function toggleLoginEnabled(){
  const sw=document.getElementById('loginEnabledSwitch');
  const willEnable=!sw.classList.contains('on');
  if(!willEnable){
    if(!confirm('关闭登录后，任何能访问该端口的人都可以控制代理，确定要关闭吗？'))return;
  }
  const r=await api('/api/account','POST',{loginEnabled:willEnable});
  if(r&&r.ok){sw.classList.toggle('on',willEnable);toast(willEnable?'已开启登录':'已关闭登录（需重新进入页面）')}
  else toast((r&&r.error)||'设置失败','error');
}

// Init
refreshStatus();setInterval(refreshStatus,3000);
trafficLoop();logLoop();
setInterval(()=>{if(document.getElementById('page-connections').classList.contains('active'))loadConnections()},5000);
setInterval(()=>{if(document.getElementById('page-nodes').classList.contains('active'))loadNodes()},5000);
</script>
</body>
</html>
"""

# ============ Login Page HTML ============
LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>xianyuvpn WebUI - 登录</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --fu-blue:#2563eb;
  --fu-blue-hover:#1d4ed8;
  --fu-blue-soft:#eff6ff;
  --fu-ink:#0f172a;
  --fu-muted:#64748b;
  --fu-line:#e2e8f0;
  --fu-page:#f5f8fc;
  --fu-white:#fff;
  --fu-danger:#dc2626;
  --fu-radius:6px;
}
body{
  font-family:"PingFang SC","HarmonyOS Sans","Segoe UI","Microsoft YaHei",sans-serif;
  background:var(--fu-page);
  color:var(--fu-ink);
  min-height:100vh;
  display:flex;
  align-items:center;
  justify-content:center;
  -webkit-font-smoothing:antialiased;
}
.login-card{
  width:400px;
  max-width:92vw;
  padding:40px 36px 32px;
  background:var(--fu-white);
  border:1px solid var(--fu-line);
  border-radius:8px;
  box-shadow:0 4px 24px rgba(0,0,0,.06);
  animation:fadeUp .3s ease;
}
@keyframes fadeUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
.login-logo{
  display:flex;
  align-items:center;
  justify-content:center;
  gap:10px;
  margin-bottom:6px;
}
.login-logo .bar{
  width:32px;height:32px;
  background:url('data:image/png;base64,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') center/contain no-repeat;
  border-radius:6px;
  position:relative;
}
.login-title{font-size:22px;font-weight:700;color:var(--fu-ink)}
.login-subtitle{
  text-align:center;
  font-size:13px;
  color:var(--fu-muted);
  margin-bottom:28px;
  margin-top:4px;
}
.form-group{margin-bottom:16px}
.form-label{
  display:block;
  font-size:13px;
  font-weight:500;
  color:var(--fu-ink);
  margin-bottom:6px;
}
.form-input{
  width:100%;
  padding:10px 14px;
  border:1px solid var(--fu-line);
  border-radius:var(--fu-radius);
  font-size:14px;
  font-family:inherit;
  color:var(--fu-ink);
  background:var(--fu-white);
  outline:none;
  transition:all .15s ease;
}
.form-input:focus{
  border-color:var(--fu-blue);
  box-shadow:0 0 0 3px rgba(37,99,235,.1);
}
.form-input::placeholder{color:#94a3b8}
.login-error{
  background:var(--fu-danger);
  color:#fff;
  font-size:13px;
  padding:10px 14px;
  border-radius:var(--fu-radius);
  margin-bottom:16px;
  display:none;
}
.login-error.show{display:block}
.login-btn{
  width:100%;
  padding:11px 0;
  border:none;
  border-radius:var(--fu-radius);
  background:var(--fu-blue);
  color:#fff;
  font-size:15px;
  font-weight:600;
  font-family:inherit;
  cursor:pointer;
  transition:all .15s;
  margin-top:4px;
}
.login-btn:hover{background:var(--fu-blue-hover)}
.login-btn:active{transform:scale(.99)}
.login-btn:disabled{opacity:.6;cursor:not-allowed;transform:none}
.captcha-row{display:flex;gap:10px}
.captcha-input{flex:1}
.captcha-btn{
  flex-shrink:0;
  padding:10px 16px;
  border:1px solid var(--fu-blue);
  border-radius:var(--fu-radius);
  background:var(--fu-white);
  color:var(--fu-blue);
  font-size:13px;
  font-weight:500;
  font-family:inherit;
  cursor:pointer;
  transition:all .15s;
  white-space:nowrap;
}
.captcha-btn:hover{background:var(--fu-blue-soft)}
.captcha-btn:disabled{opacity:.5;cursor:not-allowed}
.captcha-hint{
  font-size:12px;
  color:var(--fu-muted);
  margin-top:6px;
  line-height:1.5;
}
.login-footer{
  text-align:center;
  margin-top:20px;
  font-size:12px;
  color:var(--fu-muted);
}
</style>
</head>
<body>
<div class="login-card">
  <div class="login-logo"><span class="bar"></span><span class="login-title">xianyuvpn</span></div>
  <p class="login-subtitle">请登录以继续使用控制台</p>

  <div class="login-error" id="loginError"></div>

  <form id="loginForm" autocomplete="off">
    <div class="form-group">
      <label class="form-label" for="username">用户名</label>
      <input class="form-input" type="text" id="username" placeholder="请输入用户名" required autofocus>
    </div>
    <div class="form-group">
      <label class="form-label" for="password">密码</label>
      <input class="form-input" type="password" id="password" placeholder="请输入密码" required>
    </div>
    <div class="form-group" id="captchaGroup" style="display:none">
      <label class="form-label" for="captcha">验证码</label>
      <div class="captcha-row">
        <input class="form-input captcha-input" type="text" id="captcha" placeholder="请输入验证码" maxlength="32" autocomplete="off">
        <button class="captcha-btn" type="button" id="sendCaptchaBtn">发送验证码</button>
      </div>
      <p class="captcha-hint">新设备登录需要验证码，验证码输出在 webui 控制台，有效期 3 分钟</p>
    </div>
    <button class="login-btn" type="submit" id="loginBtn">登 录</button>
  </form>

  <p class="login-footer">xianyuvpn WebUI</p>
</div>

<script>
const form=document.getElementById('loginForm');
const errorEl=document.getElementById('loginError');
const btn=document.getElementById('loginBtn');
const captchaGroup=document.getElementById('captchaGroup');
const captchaInput=document.getElementById('captcha');
const sendCaptchaBtn=document.getElementById('sendCaptchaBtn');
let captchaRequired=false,captchaCooldown=0;

async function precheckCaptcha(){
  try{
    const res=await fetch('/api/login/status');
    if(res.ok){
      const data=await res.json();
      if(data.needCaptcha){captchaRequired=true;captchaGroup.style.display=''}
    }
  }catch(e){}
}

let authChecked=false;
async function checkAuthAndRedirect(){
  if(authChecked)return;
  authChecked=true;
  try{
    const res=await fetch('/api/status');
    if(res.status!==401){window.location.replace('/');return true}
  }catch(e){}
  precheckCaptcha();
  return false;
}
window.addEventListener('pageshow',()=>checkAuthAndRedirect());
checkAuthAndRedirect();

sendCaptchaBtn.addEventListener('click',async()=>{
  if(captchaCooldown>0)return;
  sendCaptchaBtn.disabled=true;
  sendCaptchaBtn.textContent='发送中...';
  try{
    const res=await fetch('/api/login/captcha',{method:'POST'});
    const data=await res.json();
    if(res.ok){
      errorEl.classList.remove('show');
      startCaptchaCooldown();
    }else{
      if(data.cooldown!=null)startCaptchaCooldown(data.cooldown);
      errorEl.textContent=data.error||'获取验证码失败';
      errorEl.classList.add('show');
    }
  }catch(e){
    sendCaptchaBtn.disabled=false;
    sendCaptchaBtn.textContent='发送验证码';
    errorEl.textContent='网络错误，无法发送验证码';
    errorEl.classList.add('show');
  }
});

function startCaptchaCooldown(seconds){
  captchaCooldown=seconds||60;
  sendCaptchaBtn.disabled=true;
  (function tick(){
    sendCaptchaBtn.textContent=captchaCooldown+'s 后可重发';
    captchaCooldown--;
    if(captchaCooldown>=0)setTimeout(tick,1000);
    else{sendCaptchaBtn.disabled=false;sendCaptchaBtn.textContent='发送验证码'}
  })();
}

form.addEventListener('submit',async(e)=>{
  e.preventDefault();
  errorEl.classList.remove('show');
  const username=document.getElementById('username').value.trim();
  const password=document.getElementById('password').value;
  if(!username||!password){
    errorEl.textContent='用户名和密码不能为空';
    errorEl.classList.add('show');
    return;
  }
  if(captchaRequired&&!captchaInput.value.trim()){
    errorEl.textContent='请输入验证码';
    errorEl.classList.add('show');
    return;
  }
  btn.disabled=true;btn.textContent='登录中...';
  try{
    const body={username,password};
    if(captchaRequired)body.captcha=captchaInput.value.trim();
    const res=await fetch('/api/login',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body),
    });
    const data=await res.json();
    if(res.ok&&data.ok){
      window.location.replace('/');
    }else{
      errorEl.textContent=data.error||'登录失败，请重试';
      errorEl.classList.add('show');
      if(data.requireCaptcha){
        captchaRequired=true;
        captchaGroup.style.display='';
      }
    }
  }catch(e){
    errorEl.textContent='网络错误，无法连接到服务器';
    errorEl.classList.add('show');
  }finally{
    btn.disabled=false;btn.textContent='登 录';
  }
});
</script>
</body>
</html>
"""

# ============ HTTP Handler ============
class Handler(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass

    def log_message(self, format, *args):
        pass

    def _send_json(self, data, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _send_html(self, html):
        try:
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _redirect(self, location):
        try:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _auth_required(self, path):
        """Unified guard (learned from QQBot-Web-Adapter): rate limit + public access + session.
        Returns True when the request may proceed; otherwise sends a response and returns False."""
        if not rate_limit_ok(get_client_ip(self), path in ("/api/login", "/api/login/captcha")):
            self._send_json({"error": "请求过于频繁，请稍后再试"}, 429)
            return False
        cfg = WEBUI_CONFIG or {}
        if cfg.get("login", {}).get("enabled") is False:
            return True
        # Public access control: only private IPs allowed when disabled
        if not cfg.get("publicAccess", True) and path != "/api/health":
            ip = get_client_ip(self)
            if not is_private_ip(ip):
                self._send_json({"error": "公网访问已关闭，请通过内网访问控制台"}, 403)
                return False
        if get_session(self) is None:
            if path.startswith("/api/"):
                self._send_json({"error": "未登录"}, 401)
            else:
                self._redirect("/login.html")
            return False
        return True

    def _set_cookie_headers(self, cookies):
        """cookies: list of (name, value, max_age). max_age<=0 clears the cookie."""
        for name, value, max_age in cookies:
            parts = [name + "=" + value, "Path=/", "HttpOnly", "SameSite=Lax"]
            if max_age and max_age > 0:
                parts.append("Max-Age=%d" % max_age)
                expire = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + max_age))
                parts.append("Expires=" + expire)
            else:
                parts.append("Max-Age=0")
                parts.append("Expires=Thu, 01 Jan 1970 00:00:00 GMT")
            self.send_header("Set-Cookie", "; ".join(parts))

    def _send_json_with_cookies(self, data, cookies, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self._set_cookie_headers(cookies)
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _handle_login(self, data):
        cfg = WEBUI_CONFIG or {}
        login_cfg = cfg.get("login", {})
        ip = get_client_ip(self)
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        captcha = (data.get("captcha") or "").strip()

        if not username or not password:
            self._send_json({"error": "用户名和密码不能为空"}, 400)
            return

        device_id = get_cookie(self, DEVICE_COOKIE) or ""
        device_key = device_id or ("ip:" + ip)

        with _auth_lock:
            is_blocked = device_key in _blocked
            if not is_blocked and device_id:
                is_new_device = device_id not in cfg.get("trustedDevices", {})
            else:
                is_new_device = not device_id

        need_captcha = is_blocked or is_new_device

        if need_captcha:
            if not captcha:
                self._send_json({
                    "error": "登录失败次数过多，需要输入验证码" if is_blocked else "新设备登录，需要输入验证码",
                    "requireCaptcha": True
                }, 401)
                return
            with _auth_lock:
                stored = _captchas.get(ip)
                captcha_ok = bool(stored and stored["expires"] > time.time() and hmac.compare_digest(stored["code"], captcha))
                if captcha_ok:
                    _captchas.pop(ip, None)
                    _captcha_cd.pop(ip, None)
            if not captcha_ok:
                self._send_json({"error": "验证码错误或已过期，请重新获取", "requireCaptcha": True}, 401)
                return

        # Verify credentials (constant-time compare inside verify_password)
        if username != login_cfg.get("username", "admin") or not verify_password(password, login_cfg.get("password", "")):
            with _auth_lock:
                e = _attempts.get(device_key)
                now = time.time()
                if not e or now > e["expires"]:
                    e = {"count": 0, "expires": now + LOGIN_ATTEMPTS_TTL}
                e["count"] += 1
                count = e["count"]
                _attempts[device_key] = e
                if count >= LOGIN_FAIL_THRESHOLD:
                    _blocked[device_key] = True
            print("[xianyuvpn WebUI] 登录失败 %s/%d  用户名: %s  IP: %s" % (count, LOGIN_FAIL_THRESHOLD, username, ip))
            if count >= LOGIN_FAIL_THRESHOLD:
                self._send_json({"error": "登录失败次数过多，该设备已被限制，需要输入验证码", "requireCaptcha": True}, 401)
            else:
                self._send_json({"error": "用户名或密码错误（剩余尝试 %d 次）" % (LOGIN_FAIL_THRESHOLD - count)}, 401)
            return

        # Login success
        with _auth_lock:
            _attempts.pop(device_key, None)
            if is_blocked:
                _blocked.pop(device_key, None)
            _captchas.pop(ip, None)
            _captcha_cd.pop(ip, None)
            if not device_id:
                device_id = gen_token()
            cfg.setdefault("trustedDevices", {})[device_id] = {
                "firstLogin": time.strftime("%Y-%m-%d %H:%M:%S"),
                "username": username,
            }
        save_webui_config(cfg)

        token = gen_token()
        set_session(token, username)
        secure = bool(self.headers.get("X-Forwarded-Proto", "").startswith("https"))
        print("[xianyuvpn WebUI] 登录成功  用户: %s  IP: %s" % (username, ip))
        self._send_json_with_cookies(
            {"ok": True, "username": username},
            [(SESSION_COOKIE, token, SESSION_TTL), (DEVICE_COOKIE, device_id, DEVICE_TTL)]
        )

    def _handle_login_captcha(self):
        ip = get_client_ip(self)
        with _auth_lock:
            cd = _captcha_cd.get(ip)
            if cd and time.time() < cd:
                remaining = int(cd - time.time()) + 1
                self._send_json({"error": "请等待 %d 秒后再获取验证码" % remaining, "cooldown": remaining}, 429)
                return
        code = gen_captcha()
        with _auth_lock:
            _captchas[ip] = {"code": code, "expires": time.time() + CAPTCHA_TTL}
            _captcha_cd[ip] = time.time() + CAPTCHA_COOLDOWN
        print("=" * 40)
        print("[登录验证码] 请求来自: %s" % ip)
        print("[登录验证码] 验证码: %s" % code)
        print("[登录验证码] 有效期: %d 分钟" % (CAPTCHA_TTL // 60))
        print("=" * 40)
        self._send_json({"ok": True})

    def _handle_account(self, data):
        """Read/write login credentials and access control"""
        cfg = WEBUI_CONFIG or {}
        changed = False

        username = data.get("username")
        password = data.get("password")
        public_access = data.get("publicAccess")
        login_enabled = data.get("loginEnabled")

        if username is not None:
            username = str(username).strip()
            if not username:
                self._send_json({"error": "用户名不能为空"})
                return
            if username != cfg["login"].get("username", "admin"):
                cfg["login"]["username"] = username
                changed = True
        if password:
            if len(password) < 6:
                self._send_json({"error": "密码至少 6 位"})
                return
            cfg["login"]["password"] = hash_password(password)
            changed = True
            purge_all_sessions()
        if public_access is not None:
            cfg["publicAccess"] = bool(public_access)
            changed = True
        if login_enabled is not None:
            cfg["login"]["enabled"] = bool(login_enabled)
            changed = True

        if changed:
            save_webui_config(cfg)
            self._send_json({"ok": True, "message": "已保存"})
        else:
            self._send_json({"ok": True, "message": "无变更"})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Login-free endpoints
        if path == "/api/login/status":
            device_id = get_cookie(self, DEVICE_COOKIE) or ""
            ip = get_client_ip(self)
            device_key = device_id or ("ip:" + ip)
            need = True
            if device_id:
                with _auth_lock:
                    blocked = device_key in _blocked
                    known = (not blocked) and device_id in (WEBUI_CONFIG or {}).get("trustedDevices", {})
                    need = not known
            self._send_json({"needCaptcha": need})
            return

        if path == "/login.html":
            self._send_html(LOGIN_HTML)
            return

        if path == "/api/health":
            self._send_json({"ok": True, "time": int(time.time() * 1000)})
            return

        # Everything below requires an authenticated session
        if not self._auth_required(path):
            return

        if path in ("/", "/index.html"):
            self._send_html(HTML)
            return

        if path == "/api/status":
            running = is_running()
            current_node = "-"
            version = "-"
            mode = "-"
            log_level = "info"
            allow_lan = False
            if running:
                body, code = mihomo_api("/version")
                if code == 200:
                    try: version = json.loads(body).get("version", "-")
                    except: pass
                body, code = mihomo_api("/configs")
                if code == 200:
                    try:
                        cfg = json.loads(body)
                        mode = cfg.get("mode", "-")
                        log_level = cfg.get("log-level", "info")
                        allow_lan = bool(cfg.get("allow-lan", False))
                    except: pass
                body, code = mihomo_api("/proxies")
                if code == 200:
                    try:
                        proxies = json.loads(body).get("proxies", {})
                        group_types = ("Selector", "URLTest", "Fallback", "LoadBalance")
                        # Follow the chain from GLOBAL down to a real node
                        cur = (proxies.get("GLOBAL") or {}).get("now")
                        hops = 0
                        while cur and cur in proxies and proxies[cur].get("type") in group_types and hops < 16:
                            nxt = proxies[cur].get("now")
                            if not nxt or nxt == cur:
                                break
                            cur = nxt
                            hops += 1
                        if cur and cur not in ("DIRECT", "REJECT"):
                            current_node = cur
                        elif cur == "DIRECT":
                            current_node = "直连"
                        elif cur == "REJECT":
                            current_node = "拒绝"
                    except: pass
            uptime = None
            pid = find_mihomo_pid()
            if pid is not None:
                uptime = process_uptime(pid)
            self._send_json({
                "running": running, "current_node": current_node,
                "version": version, "mode": mode, "log_level": log_level,
                "allow_lan": allow_lan, "project_dir": PROJECT_DIR,
                "uptime": uptime
            })
            return

        if path == "/api/proxies":
            if not is_running():
                self._send_json({"groups": [], "proxies": []})
                return
            body, code = mihomo_api("/proxies")
            if code == 200:
                data = json.loads(body)
                proxies = data.get("proxies", {})
                group_types = ("Selector", "URLTest", "Fallback", "LoadBalance")
                groups = []
                for name, info in proxies.items():
                    # mihomo reports group types as Selector/URLTest/Fallback/LoadBalance
                    if info.get("type") not in group_types or name == "GLOBAL":
                        continue
                    group_proxies = []
                    for pname in info.get("all", []):
                        if pname in proxies:
                            group_proxies.append({"name": pname, "type": proxies[pname].get("type", "")})
                    groups.append({"name": name, "type": info.get("type"), "now": info.get("now", ""), "all": group_proxies})
                self._send_json({"groups": groups})
            else:
                self._send_json({"groups": [], "error": body}, code)
            return

        if path.startswith("/api/proxies/") and path.endswith("/delay"):
            node_name = unquote(path[len("/api/proxies/"):-len("/delay")])
            body, code = mihomo_api("/proxies/" + quote(node_name, safe="") + "/delay?timeout=5000&url=http://www.gstatic.com/generate_204")
            if code == 200:
                self._send_json(json.loads(body))
            else:
                self._send_json({"delay": 0, "error": body}, code)
            return

        if path == "/api/connections":
            if not is_running():
                self._send_json({"connections": []})
                return
            body, code = mihomo_api("/connections")
            if code == 200:
                data = json.loads(body)
                if data.get("connections") is None:
                    data["connections"] = []
                self._send_json(data)
            else:
                self._send_json({"connections": []}, code)
            return

        if path == "/api/rules":
            if not is_running():
                self._send_json({"rules": []})
                return
            body, code = mihomo_api("/rules")
            if code == 200:
                self._send_json(json.loads(body))
            else:
                self._send_json({"rules": []}, code)
            return

        if path == "/api/configs":
            body, code = mihomo_api("/configs")
            if code == 200:
                self._send_json(json.loads(body))
            else:
                self._send_json({}, code)
            return

        if path == "/api/base-config":
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
                self._send_json({"content": content})
            except Exception as e:
                self._send_json({"content": "", "error": str(e)})
            return

        if path == "/api/account":
            cfg = WEBUI_CONFIG or {}
            self._send_json({
                "username": cfg.get("login", {}).get("username", "admin"),
                "publicAccess": cfg.get("publicAccess", True),
                "loginEnabled": cfg.get("login", {}).get("enabled", True),
            })
            return

        if path == "/api/traffic":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                req = urllib.request.Request(MIHOMO_API + "/traffic")
                for k, v in mihomo_headers().items():
                    req.add_header(k, v)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    while True:
                        # mihomo pushes one small JSON object per line; readline
                        # avoids read(N) blocking until N bytes accumulate
                        line = resp.readline()
                        if not line: break
                        self.wfile.write(line)
                        self.wfile.flush()
            except: pass
            return

        if path == "/api/logs":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                if os.path.exists(LOG_FILE):
                    with open(LOG_FILE, "r", errors="replace") as f:
                        f.seek(max(0, os.path.getsize(LOG_FILE) - 4096))
                        self.wfile.write(f.read().encode("utf-8", errors="replace"))
                        self.wfile.flush()
                last_size = os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0
                while True:
                    time.sleep(0.8)
                    if os.path.exists(LOG_FILE):
                        cur_size = os.path.getsize(LOG_FILE)
                        if cur_size > last_size:
                            with open(LOG_FILE, "r", errors="replace") as f:
                                f.seek(last_size)
                                self.wfile.write(f.read().encode("utf-8", errors="replace"))
                                self.wfile.flush()
                            last_size = cur_size
                        elif cur_size < last_size:
                            last_size = 0
            except: pass
            return

        self._send_json({"error": "Not found: " + path}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode() if content_length else "{}"
        try: data = json.loads(body) if body else {}
        except: data = {}

        if path in ("/api/login", "/api/logout", "/api/login/captcha"):
            is_login_path = path in ("/api/login", "/api/login/captcha")
            if not rate_limit_ok(get_client_ip(self), is_login_path):
                self._send_json({"error": "请求过于频繁，请稍后再试"}, 429)
                return

        if path == "/api/login":
            self._handle_login(data)
            return

        if path == "/api/logout":
            token = get_cookie(self, SESSION_COOKIE)
            if token:
                delete_session(token)
            self._send_json_with_cookies({"ok": True}, [(SESSION_COOKIE, "", 0)])
            return

        if path == "/api/login/captcha":
            self._handle_login_captcha()
            return

        # All endpoints below require an authenticated session
        if not self._auth_required(path):
            return

        if path == "/api/start":
            success, msg = start_mihomo()
            self._send_json({"success": success, "message": msg})
            return

        if path == "/api/stop":
            success, msg = stop_mihomo()
            self._send_json({"success": success, "message": msg})
            return

        if path == "/api/restart":
            success, msg = restart_mihomo()
            self._send_json({"success": success, "message": msg})
            return

        if path == "/api/reload":
            if not is_running():
                self._send_json({"success": False, "message": "Not running"})
                return
            # mihomo only allows reloading configs inside the data dir (SAFE_PATHS),
            # so copy config.yaml into data/ and reload via absolute path inside data.
            try:
                import shutil
                data_config = os.path.join(DATA_DIR, "config.yaml")
                shutil.copy2(CONFIG_FILE, data_config)
                body, code = mihomo_api("/configs?force=true", "PUT", {"path": data_config})
                self._send_json({"success": code in (200, 204), "message": "Config reloaded" if code in (200, 204) else body})
            except Exception as e:
                self._send_json({"success": False, "message": str(e)})
            return

        if path.startswith("/api/proxies/") and path.endswith("/select"):
            group_name = unquote(path[len("/api/proxies/"):-len("/select")])
            node_name = data.get("name", "")
            if not node_name:
                self._send_json({"success": False, "message": "No node name"})
                return
            body, code = mihomo_api("/proxies/" + quote(group_name, safe=""), "PUT", {"name": node_name})
            self._send_json({"success": code in (200, 204), "message": "Selected" if code in (200, 204) else body})
            return

        if path == "/api/update-sub":
            sub_url = data.get("url", "").strip()
            if not sub_url and os.path.exists(SUB_FILE):
                try:
                    with open(SUB_FILE) as f: sub_url = f.read().strip()
                except: pass
            if not sub_url:
                self._send_json({"success": False, "message": "No subscription URL"})
                return
            try:
                result = subprocess.run(
                    ["bash", UPDATE_SCRIPT, sub_url],
                    capture_output=True, text=True, cwd=PROJECT_DIR, timeout=60
                )
                success = result.returncode == 0
                msg = (result.stdout + result.stderr).strip()[-300:]
                self._send_json({"success": success, "message": "订阅更新成功" if success else "更新失败: " + msg})
            except Exception as e:
                self._send_json({"success": False, "message": str(e)})
            return

        if path == "/api/base-config":
            content = data.get("content", "")
            try:
                import yaml
            except ImportError:
                yaml = None
            if yaml is not None:
                try:
                    parsed = yaml.safe_load(content)
                    if not isinstance(parsed, dict):
                        self._send_json({"success": False, "message": "YAML 格式错误：不是有效的配置映射"})
                        return
                except Exception as e:
                    self._send_json({"success": False, "message": "YAML 解析失败，未保存: " + str(e)[:200]})
                    return
            try:
                tmp = CONFIG_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp, CONFIG_FILE)
                # Hot reload if running
                msg = "配置已保存"
                if is_running():
                    try:
                        import shutil
                        data_config = os.path.join(DATA_DIR, "config.yaml")
                        shutil.copy2(CONFIG_FILE, data_config)
                        body, code = mihomo_api("/configs?force=true", "PUT", {"path": data_config})
                        if code in (200, 204):
                            msg = "配置已保存并热重载"
                        else:
                            ok, _ = restart_mihomo()
                            msg = "配置已保存并重启生效" if ok else "配置已保存，但重载失败: " + str(body)[:200]
                    except Exception as reload_err:
                        ok, _ = restart_mihomo()
                        msg = "配置已保存并重启生效" if ok else "配置已保存，但重载失败: " + str(reload_err)[:200]
                self._send_json({"success": True, "message": msg})
            except Exception as e:
                self._send_json({"success": False, "message": str(e)})
            return

        if path == "/api/allow-lan":
            enabled = data.get("enabled", False)
            try:
                value = "true" if enabled else "false"
                # 1) Persist to base.yaml and config.yaml so it survives regen/restart
                for target in (BASE_FILE, CONFIG_FILE):
                    if not os.path.exists(target):
                        continue
                    with open(target, "r", encoding="utf-8") as f:
                        content = f.read()
                    if re.search(r'^allow-lan:', content, flags=re.MULTILINE):
                        content = re.sub(r'^allow-lan:.*$', 'allow-lan: ' + value, content, flags=re.MULTILINE)
                    else:
                        content = re.sub(r'^(mixed-port:.*)$', r'\1\nallow-lan: ' + value, content, count=1, flags=re.MULTILINE)
                    with open(target, "w", encoding="utf-8") as f:
                        f.write(content)
                # 2) Apply at runtime via API (no restart needed); fallback to restart
                msg_suffix = ""
                if is_running():
                    body, code = mihomo_api("/configs", "PATCH", {"allow-lan": enabled})
                    if code in (200, 204):
                        msg_suffix = "，已即时生效"
                    else:
                        ok, _ = restart_mihomo()
                        msg_suffix = "，已重启生效" if ok else "，但应用失败，请手动重启"
                self._send_json({"success": True, "message": "允许局域网已" + ("开启" if enabled else "关闭") + msg_suffix})
            except Exception as e:
                self._send_json({"success": False, "message": str(e)})
            return

        if path == "/api/account":
            self._handle_account(data)
            return

        self._send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode() if content_length else "{}"
        try: data = json.loads(body) if body else {}
        except: data = {}

        if not self._auth_required(path):
            return

        if path == "/api/configs":
            body, code = mihomo_api("/configs", "PATCH", data)
            self._send_json({"success": code in (200, 204), "message": "Updated"})
            return

        self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if not self._auth_required(path):
            return

        if path == "/api/connections":
            body, code = mihomo_api("/connections", "DELETE")
            self._send_json({"success": code in (200, 204)})
            return

        if path.startswith("/api/connections/"):
            conn_id = unquote(path.split("/")[-1])
            body, code = mihomo_api("/connections/" + conn_id, "DELETE")
            self._send_json({"success": code in (200, 204)})
            return

        self._send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

# ============ Main ============
def main():
    global WEB_HOST, WEB_PORT, WEBUI_CONFIG
    import argparse
    parser = argparse.ArgumentParser(description="xianyuvpn WebUI")
    parser.add_argument("--host", default=WEB_HOST, help="Listen host")
    parser.add_argument("--port", type=int, default=WEB_PORT, help="Listen port")
    args = parser.parse_args()

    # Load (or initialize) WebUI auth config
    WEBUI_CONFIG = load_webui_config()
    save_webui_config(WEBUI_CONFIG)

    # Background cleanup for expired sessions/captcha/rate-limit entries
    t = threading.Thread(target=_cleanup_loop, daemon=True)
    t.start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"xianyuvpn WebUI started at http://{args.host}:{args.port}")
    print(f"Project dir: {PROJECT_DIR}")
    if WEBUI_CONFIG.get("login", {}).get("enabled", True):
        print("Login enabled. Default credentials: admin / admin123 (change after first login!)")
    else:
        print("Login disabled — console is open to everyone who can reach this port.")
    print(f"Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        server.server_close()

if __name__ == "__main__":
    main()
