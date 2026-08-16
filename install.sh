#!/usr/bin/env bash
# ============================================================
# xianyuvpn - install script
# Downloads Mihomo core and GeoIP/GeoSite data into current directory
# Usage: ./install.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

MIHOMO_VERSION="v1.19.29"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  xianyuvpn Installer${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Detect architecture
ARCH=$(uname -m)
case "$ARCH" in
    x86_64|amd64)   ARCH="amd64" ;;
    aarch64|arm64)  ARCH="arm64" ;;
    armv7l)         ARCH="armv7" ;;
    *)
        echo -e "${RED}Unsupported architecture: $ARCH${NC}"
        exit 1
        ;;
esac
echo -e "Architecture: $ARCH"
echo -e "Install path: $SCRIPT_DIR"
echo ""

# Create directories
echo "[1/3] Creating directories..."
mkdir -p bin data config

# Download mihomo core (using ghproxy mirror for China users)
echo "[2/3] Downloading Mihomo core $MIHOMO_VERSION..."
TMP_FILE=$(mktemp)
MIHOMO_URL="https://gh.jasonzeng.dev/https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/mihomo-linux-${ARCH}-${MIHOMO_VERSION}.gz"
echo "  URL: $MIHOMO_URL"
curl -sL -o "$TMP_FILE" "$MIHOMO_URL"
if [ ! -s "$TMP_FILE" ]; then
    echo -e "${RED}Download failed, trying direct GitHub...${NC}"
    MIHOMO_URL="https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/mihomo-linux-${ARCH}-${MIHOMO_VERSION}.gz"
    curl -sL -o "$TMP_FILE" "$MIHOMO_URL"
fi
if [ ! -s "$TMP_FILE" ]; then
    echo -e "${RED}Download failed, please check network connection${NC}"
    rm -f "$TMP_FILE"
    exit 1
fi
gunzip -c "$TMP_FILE" > bin/mihomo
chmod +x bin/mihomo
rm -f "$TMP_FILE"
echo -e "  ${GREEN}Core installed${NC}"

# Download GeoIP/GeoSite data
echo "[3/3] Downloading GeoIP/GeoSite data..."
GEO_BASE="https://gh.jasonzeng.dev/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest"
GEO_DIRECT="https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest"

download_geo() {
    local filename="$1"
    local target="$2"
    echo -n "  Downloading $filename... "
    if curl -sL --connect-timeout 15 --max-time 120 -o "$target" "$GEO_BASE/$filename" && [ -s "$target" ]; then
        echo -e "${GREEN}OK${NC}"
        return 0
    fi
    # Retry without mirror
    if curl -sL --connect-timeout 15 --max-time 120 -o "$target" "$GEO_DIRECT/$filename" && [ -s "$target" ]; then
        echo -e "${GREEN}OK (direct)${NC}"
        return 0
    fi
    echo -e "${YELLOW}FAILED${NC}"
    return 1
}

# metadb format (for geodata-mode: true)
download_geo "geoip.metadb" "data/geoip.metadb" || true
download_geo "geosite.dat" "data/geosite.dat" || true
download_geo "ASN.mmdb" "data/ASN.mmdb" || true

# Standard format (for GEOIP/GEOSITE rules in subscriptions)
# These are REQUIRED if subscriptions use GEOIP or GEOSITE rule types
download_geo "GeoIP.dat" "data/GeoIP.dat" || echo -e "  ${RED}WARNING: GeoIP.dat download failed! GEOIP rules will not work.${NC}"
download_geo "geosite.db" "data/geosite.db" || true

# Remove stale uppercase-named files from old installs
rm -f data/GEOIP.metadb 2>/dev/null || true

echo -e "  ${GREEN}Geo data downloaded${NC}"

# Make scripts executable
chmod +x xy scripts/*.sh 2>/dev/null || true

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Next steps:"
echo "  1. chmod +x xy scripts/*.sh"
echo "  2. xy update <your-subscription-url>"
echo "  3. xy start"
echo "  4. eval \$(xy proxy)   # enable proxy for current shell"
echo ""
echo "WebUI: xy webui   (then open http://localhost:9091)"
echo "Manage: xy {start|stop|restart|status|reload|log|update|webui}"
