#!/usr/bin/env bash
# ============================================================
# xianyuvpn - subscription update script
# Usage: ./update-sub.sh <subscription-url>
# Or save URL to config/sub.txt and run ./update-sub.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_DIR="$PROJECT_DIR/config"
SUB_FILE="$CONFIG_DIR/sub.txt"
BASE_FILE="$CONFIG_DIR/base.yaml"
OUT_FILE="$CONFIG_DIR/config.yaml"

# Get subscription URL
SUB_URL="${1:-}"
if [ -z "$SUB_URL" ] && [ -f "$SUB_FILE" ]; then
    SUB_URL="$(cat "$SUB_FILE" | tr -d '[:space:]')"
fi
if [ -z "$SUB_URL" ]; then
    echo "[ERROR] No subscription URL provided"
    echo "Usage: $0 <subscription-url>"
    echo "Or save URL to $SUB_FILE"
    exit 1
fi

# Save subscription URL for later use
echo "$SUB_URL" > "$SUB_FILE"

echo "[1/3] Downloading subscription config..."
TMP_SUB=$(mktemp)
HTTP_CODE=$(curl -sL -w "%{http_code}" -o "$TMP_SUB" "$SUB_URL" || true)
if [ "$HTTP_CODE" != "200" ]; then
    echo "[ERROR] Download failed, HTTP code: $HTTP_CODE"
    rm -f "$TMP_SUB"
    exit 1
fi
if [ ! -s "$TMP_SUB" ]; then
    echo "[ERROR] Downloaded file is empty"
    rm -f "$TMP_SUB"
    exit 1
fi

# Detect and decode base64-encoded subscriptions (most providers use this format)
# If the file starts with valid YAML structure, skip decoding
TMP_DECODED=$(mktemp)
NEED_DECODE=false

# Check if it's already valid YAML (starts with typical YAML keys)
if head -20 "$TMP_SUB" | grep -qE '^(mixed-port|port|proxies|proxy-groups|rules|dns):'; then
    cp "$TMP_SUB" "$TMP_DECODED"
else
    # Try base64 decode
    if base64 -d "$TMP_SUB" > "$TMP_DECODED" 2>/dev/null && [ -s "$TMP_DECODED" ]; then
        NEED_DECODE=true
    elif base64 --decode "$TMP_SUB" > "$TMP_DECODED" 2>/dev/null && [ -s "$TMP_DECODED" ]; then
        NEED_DECODE=true
    else
        # Not valid base64 either — use as-is and let the merge step report errors
        cp "$TMP_SUB" "$TMP_DECODED"
    fi
fi

# After decoding, check if it's raw node links (not Clash config)
if grep -qE '^(vmess|vless|trojan|ss|ssr|hysteria2?|tuic)://' "$TMP_DECODED"; then
    echo "[ERROR] Subscription returns node links, not Clash config"
    echo "Please add ?clash=1 to the subscription URL or contact your provider"
    echo "Sample content (first 3 lines):"
    head -3 "$TMP_DECODED"
    rm -f "$TMP_SUB" "$TMP_DECODED"
    exit 1
fi

# Quick sanity check: decoded content should contain YAML proxy-related keys
if ! grep -qE '(proxies|proxy-groups|rules|mixed-port|port):' "$TMP_DECODED"; then
    echo "[WARNING] Decoded content does not look like a Clash config"
    echo "First 5 lines:"
    head -5 "$TMP_DECODED"
    echo ""
    echo "Continuing anyway..."
fi

echo "[2/3] Merging config..."
python3 - "$BASE_FILE" "$TMP_DECODED" "$OUT_FILE" <<'PYEOF'
import sys, yaml

base_path, sub_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

with open(base_path, 'r') as f:
    base = yaml.safe_load(f) or {}
with open(sub_path, 'r') as f:
    sub = yaml.safe_load(f) or {}

if not sub:
    print("[ERROR] Subscription content is empty or not valid YAML")
    sys.exit(1)

merged = dict(base)
for key in ('proxies', 'proxy-groups', 'proxy-providers', 'rules', 'rule-providers'):
    if key in sub:
        merged[key] = sub[key]

if 'port' in sub and 'mixed-port' not in merged:
    merged.pop('port', None)
    merged.pop('socks-port', None)

if 'proxy-groups' in merged and merged['proxy-groups']:
    for g in merged['proxy-groups']:
        if 'proxies' not in g or not g['proxies']:
            g['proxies'] = ['DIRECT', 'REJECT']

proxy_count = len(merged.get('proxies', []))
group_count = len(merged.get('proxy-groups', []))
rule_count = len(merged.get('rules', []))

if proxy_count == 0 and group_count == 0 and rule_count == 0:
    print("[ERROR] No proxies/groups/rules found in subscription")
    print(f"  Subscription keys: {list(sub.keys())}")
    sys.exit(1)

with open(out_path, 'w') as f:
    yaml.dump(merged, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print(f"[OK] Proxies: {proxy_count}, Groups: {group_count}, Rules: {rule_count}")
PYEOF

MERGE_EXIT=$?
rm -f "$TMP_SUB" "$TMP_DECODED"

if [ $MERGE_EXIT -ne 0 ]; then
    echo "[ERROR] Config merge failed (exit code: $MERGE_EXIT)"
    exit 1
fi

echo "[3/3] Config saved to $OUT_FILE"
echo ""
echo "Start proxy: xy start"
echo "Reload config: xy reload"
