#!/usr/bin/env bash
#
# Настройка VPN для доступа к камерам:
#   VPS (Tailscale + WireGuard) ←→ Keenetic (WireGuard) → LAN → DVR
#
# Что делает скрипт:
#   1. Включает IP forwarding
#   2. Генерирует WireGuard ключи (VPS + Keenetic)
#   3. Создаёт конфигурацию WireGuard для VPS
#   4. Выводит пошаговую инструкцию для Keenetic
#   5. Запрашивает Tailscale auth key
#   6. Поднимает контейнеры: wireguard, tailscale, mediamtx
#   7. Проверяет связь с камерами
#   8. Закрывает RTSP-порт от публичного доступа
#
# Использование:
#   chmod +x setup-vpn.sh && ./setup-vpn.sh
#
set -euo pipefail
cd "$(dirname "$0")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

info()   { echo -e "${CYAN}▸${NC} $*"; }
ok()     { echo -e "${GREEN}✔${NC} $*"; }
warn()   { echo -e "${YELLOW}!${NC} $*"; }
err()    { echo -e "${RED}✖${NC} $*" >&2; }
header() { echo -e "\n${BOLD}=== $* ===${NC}"; }

# Load .env
set -a; source .env 2>/dev/null || true; set +a

WG_PORT="${WG_PORT:-51820}"
WG_VPS_ADDR="${WG_VPS_ADDR:-10.0.0.1/24}"
WG_KEENETIC_ADDR="${WG_KEENETIC_ADDR:-10.0.0.2/24}"
LAN_SUBNET="${LAN_SUBNET:-192.168.1.0/24}"
WG_DIR="./wireguard/wg_confs"

# ─── Prerequisites ──────────────────────────────────────────

header "Проверка зависимостей"

command -v docker >/dev/null || { err "Docker не найден"; exit 1; }
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif docker-compose version >/dev/null 2>&1; then
  DC="docker-compose"
else
  err "Docker Compose не найден"; exit 1
fi
ok "Docker + Compose"

# ─── IP forwarding ──────────────────────────────────────────

header "IP forwarding"

SYSCTL_CONF="/etc/sysctl.d/99-vpn.conf"
NEED_SYSCTL=false

if [ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)" != "1" ]; then
  sudo sysctl -w net.ipv4.ip_forward=1 >/dev/null
  sudo sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1 || true
  NEED_SYSCTL=true
fi

if [ ! -f "$SYSCTL_CONF" ] || ! grep -q 'net.ipv4.ip_forward' "$SYSCTL_CONF" 2>/dev/null; then
  printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' \
    | sudo tee "$SYSCTL_CONF" >/dev/null
  sudo sysctl -p "$SYSCTL_CONF" >/dev/null 2>&1 || true
fi
ok "IP forwarding включён"

# ─── WireGuard keys ─────────────────────────────────────────

header "WireGuard"

mkdir -p "$WG_DIR"

# Detect public IP
VPS_IP=$(curl -s4 --connect-timeout 5 ifconfig.me 2>/dev/null \
      || curl -s4 --connect-timeout 5 icanhazip.com 2>/dev/null \
      || echo "")
VPS_IP=$(echo "$VPS_IP" | tr -d '[:space:]')

if [ -z "$VPS_IP" ]; then
  read -rp "Не удалось определить публичный IP VPS. Введите вручную: " VPS_IP
fi
ok "Публичный IP VPS: ${BOLD}${VPS_IP}${NC}"

# Generate keys if config doesn't exist
if [ -f "$WG_DIR/wg0.conf" ]; then
  warn "Конфигурация $WG_DIR/wg0.conf уже существует — пропускаю генерацию ключей"
  warn "Удалите файл и перезапустите скрипт, если нужно перегенерировать"
  VPS_PUBKEY=$(grep -A1 '\[Interface\]' "$WG_DIR/wg0.conf" | grep PrivateKey | awk '{print $3}' \
    | docker run --rm -i --entrypoint sh lscr.io/linuxserver/wireguard -c 'wg pubkey' 2>/dev/null || echo "???")
else
  info "Генерация ключей..."

  # Pull image for wg tool
  docker pull -q lscr.io/linuxserver/wireguard:latest >/dev/null 2>&1 || true
  WG_CMD="docker run --rm --entrypoint sh lscr.io/linuxserver/wireguard -c"

  VPS_PRIVKEY=$($WG_CMD 'wg genkey')
  VPS_PUBKEY=$(echo "$VPS_PRIVKEY" | $WG_CMD 'wg pubkey')
  KEEN_PRIVKEY=$($WG_CMD 'wg genkey')
  KEEN_PUBKEY=$(echo "$KEEN_PRIVKEY" | $WG_CMD 'wg pubkey')

  ok "Ключи сгенерированы"

  # Create VPS WireGuard config
  cat > "$WG_DIR/wg0.conf" <<WGEOF
[Interface]
PrivateKey = ${VPS_PRIVKEY}
Address = ${WG_VPS_ADDR}
ListenPort = ${WG_PORT}

[Peer]
# Keenetic router
PublicKey = ${KEEN_PUBKEY}
AllowedIPs = ${WG_KEENETIC_ADDR%/*}/32, ${LAN_SUBNET}
WGEOF
  chmod 600 "$WG_DIR/wg0.conf"
  ok "Конфигурация VPS создана: ${DIM}$WG_DIR/wg0.conf${NC}"

  # Save Keenetic key for reference
  echo "$KEEN_PRIVKEY" > "$WG_DIR/keenetic.key"
  chmod 600 "$WG_DIR/keenetic.key"

  # ─── Keenetic instructions ──────────────────────────────────

  header "Настройка Keenetic"

  echo ""
  echo -e "${BOLD}Откройте веб-интерфейс роутера → Другие подключения → WireGuard${NC}"
  echo ""
  echo -e "${BOLD}Шаг 1.${NC} Если WireGuard нет в списке:"
  echo "  Управление → Общие настройки → Изменить набор компонентов"
  echo "  → найти «WireGuard VPN» → установить → перезагрузить"
  echo ""
  echo -e "${BOLD}Шаг 2.${NC} Добавить подключение → Имя: ${CYAN}VPS${NC}"
  echo ""
  echo -e "${BOLD}Шаг 3.${NC} Настройки подключения:"
  echo -e "  Приватный ключ:  ${YELLOW}${KEEN_PRIVKEY}${NC}"
  echo -e "  Адрес:           ${CYAN}${WG_KEENETIC_ADDR}${NC}"
  echo -e "  Порт:            ${DIM}(оставить пустым)${NC}"
  echo -e "  DNS:             ${DIM}(оставить пустым)${NC}"
  echo ""
  echo -e "${BOLD}Шаг 4.${NC} Добавить пир:"
  echo -e "  Публичный ключ:       ${YELLOW}${VPS_PUBKEY}${NC}"
  echo -e "  Endpoint:             ${CYAN}${VPS_IP}:${WG_PORT}${NC}"
  echo -e "  Разрешённые подсети:  ${CYAN}${WG_VPS_ADDR%/*}/32${NC}"
  echo -e "  Проверка активности:  ${CYAN}25${NC}"
  echo ""
  echo -e "${BOLD}Шаг 5.${NC} Сохранить и ${GREEN}включить${NC} подключение."
  echo ""
  echo -e "  ${DIM}«Использовать для выхода в интернет» — НЕ включать!${NC}"
  echo ""
  echo "────────────────────────────────────────────────────"
fi

# ─── Open WireGuard port ────────────────────────────────────

header "Firewall: WireGuard порт ${WG_PORT}/udp"

if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow "${WG_PORT}/udp" >/dev/null 2>&1 && ok "UFW: порт ${WG_PORT}/udp открыт"
elif command -v iptables >/dev/null 2>&1; then
  sudo iptables -C INPUT -p udp --dport "${WG_PORT}" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT -p udp --dport "${WG_PORT}" -j ACCEPT
  ok "iptables: порт ${WG_PORT}/udp открыт"
else
  warn "Откройте порт ${WG_PORT}/udp вручную в firewall"
fi

# ─── Tailscale auth key ────────────────────────────────────

header "Tailscale"

if grep -q 'TS_AUTHKEY=tskey-auth-REPLACE_ME' .env 2>/dev/null; then
  echo ""
  info "Нужен auth key:"
  echo "  1. https://login.tailscale.com/admin/settings/keys"
  echo "  2. Generate auth key → Reusable: да → Generate"
  echo ""
  read -rp "Вставьте auth key: " TS_KEY
  [ -z "$TS_KEY" ] && { err "Ключ не может быть пустым"; exit 1; }
  sed -i "s|TS_AUTHKEY=.*|TS_AUTHKEY=${TS_KEY}|" .env
  ok "Ключ сохранён в .env"
else
  ok "Auth key уже задан"
fi

# ─── Start containers ──────────────────────────────────────

header "Запуск контейнеров"

info "wireguard + tailscale + mediamtx..."
$DC up -d wireguard tailscale mediamtx
ok "Контейнеры запущены"

# ─── Wait & verify ──────────────────────────────────────────

header "Проверка подключений"

# Tailscale
info "Tailscale..."
for _ in $(seq 1 15); do
  TS_IP=$($DC exec -T tailscale tailscale ip -4 2>/dev/null | tr -d '[:space:]') && break
  sleep 1
done

if [ -n "${TS_IP:-}" ]; then
  ok "Tailscale IP: ${BOLD}${TS_IP}${NC}"
else
  warn "Tailscale ещё подключается — проверьте позже: docker compose logs tailscale"
  TS_IP="<tailscale-ip>"
fi

# WireGuard
info "WireGuard туннель..."
sleep 3
WG_PEER_OK=false
for _ in $(seq 1 5); do
  if ping -c1 -W2 "${WG_KEENETIC_ADDR%/*}" >/dev/null 2>&1; then
    WG_PEER_OK=true; break
  fi
  sleep 2
done

if $WG_PEER_OK; then
  ok "Keenetic доступен через туннель (${WG_KEENETIC_ADDR%/*})"
else
  warn "Keenetic пока не отвечает — проверьте настройки на роутере"
  warn "После настройки Keenetic проверьте: ping ${WG_KEENETIC_ADDR%/*}"
fi

# Camera DVR
DVR_IP="${LAN_SUBNET%.*}.2"
info "DVR (${DVR_IP}:554)..."
if $WG_PEER_OK && timeout 3 bash -c "echo >/dev/tcp/${DVR_IP}/554" 2>/dev/null; then
  ok "DVR ${DVR_IP}:554 доступен"
else
  if $WG_PEER_OK; then
    warn "DVR ${DVR_IP}:554 не отвечает — проверьте IP и порт камер"
  else
    warn "Пропуск (нет связи с Keenetic)"
  fi
fi

# ─── Firewall: RTSP ────────────────────────────────────────

header "Firewall: RTSP 8554/tcp"

if command -v iptables >/dev/null 2>&1; then
  sudo iptables -D INPUT -p tcp --dport 8554 -i tailscale0 -j ACCEPT 2>/dev/null || true
  sudo iptables -D INPUT -p tcp --dport 8554 -j DROP 2>/dev/null || true
  sudo iptables -I INPUT -p tcp --dport 8554 -i tailscale0 -j ACCEPT
  sudo iptables -A INPUT -p tcp --dport 8554 -j DROP
  ok "RTSP порт 8554 открыт только через Tailscale"

  if command -v iptables-save >/dev/null 2>&1; then
    if [ -d /etc/iptables ]; then
      sudo iptables-save | sudo tee /etc/iptables/rules.v4 >/dev/null 2>&1 || true
    elif command -v netfilter-persistent >/dev/null 2>&1; then
      sudo netfilter-persistent save >/dev/null 2>&1 || true
    fi
  fi
else
  warn "Закройте порт 8554 на публичном интерфейсе вручную"
fi

# ─── Summary ───────────────────────────────────────────────

header "Готово!"
echo ""
echo -e "  ${DIM}Схема:${NC}"
echo -e "  VLC → Tailscale → ${BOLD}VPS${NC} (mediamtx:8554) → WireGuard → ${BOLD}Keenetic${NC} → LAN → DVR"
echo ""
echo -e "  Tailscale IP:   ${BOLD}${GREEN}${TS_IP}${NC}"
echo -e "  WireGuard:      VPS ${WG_VPS_ADDR} ←→ Keenetic ${WG_KEENETIC_ADDR}"
echo ""
echo -e "  ${BOLD}Камеры в VLC:${NC}"
echo -e "  ${CYAN}rtsp://${TS_IP}:8554/cam1${NC}  ← /10"
echo -e "  ${CYAN}rtsp://${TS_IP}:8554/cam2${NC}  ← /20"
echo ""

if ! $WG_PEER_OK; then
  echo -e "  ${YELLOW}⚠  WireGuard туннель ещё не установлен.${NC}"
  echo -e "  ${YELLOW}   Настройте Keenetic по инструкции выше и проверьте:${NC}"
  echo -e "  ${CYAN}   ping ${WG_KEENETIC_ADDR%/*}${NC}"
  echo ""
fi
