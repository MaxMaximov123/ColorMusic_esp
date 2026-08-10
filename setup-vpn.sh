#!/usr/bin/env bash
#
# Автоматическая настройка Tailscale VPN + RTSP relay на VPS.
#
# Что делает скрипт:
#   1. Включает IP forwarding (нужно для маршрутизации подсетей)
#   2. Запрашивает Tailscale auth key (если ещё не задан)
#   3. Поднимает контейнеры tailscale + mediamtx
#   4. Ждёт подключения к Tailscale
#   5. Закрывает порт RTSP (8554) от публичного доступа — оставляет только через Tailscale
#   6. Выводит Tailscale IP и инструкции
#
# Использование:
#   chmod +x setup-vpn.sh
#   ./setup-vpn.sh
#
set -euo pipefail
cd "$(dirname "$0")"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✔${NC} $*"; }
err()   { echo -e "${RED}✖${NC} $*" >&2; }
header(){ echo -e "\n${BOLD}=== $* ===${NC}"; }

# --- Prerequisites ---
header "Проверка зависимостей"

for cmd in docker; do
  command -v "$cmd" >/dev/null || { err "$cmd не найден. Установите Docker."; exit 1; }
done

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif docker-compose version >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  err "Docker Compose не найден"; exit 1
fi
ok "Docker + Compose"

# --- IP forwarding ---
header "IP forwarding"

SYSCTL_CONF="/etc/sysctl.d/99-tailscale.conf"
if [ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)" != "1" ]; then
  info "Включаю ip_forward..."
  sudo sysctl -w net.ipv4.ip_forward=1 >/dev/null
  sudo sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1 || true
fi

if [ ! -f "$SYSCTL_CONF" ] || ! grep -q 'net.ipv4.ip_forward' "$SYSCTL_CONF" 2>/dev/null; then
  info "Сохраняю в $SYSCTL_CONF для автозагрузки..."
  printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' | sudo tee "$SYSCTL_CONF" >/dev/null
  sudo sysctl -p "$SYSCTL_CONF" >/dev/null 2>&1 || true
fi
ok "IP forwarding включён"

# --- Tailscale auth key ---
header "Tailscale auth key"

if grep -q 'TS_AUTHKEY=tskey-auth-REPLACE_ME' .env 2>/dev/null || grep -q 'TS_AUTHKEY=$' .env 2>/dev/null; then
  echo ""
  info "Нужен auth key. Создайте его:"
  echo "  1. Откройте https://login.tailscale.com/admin/settings/keys"
  echo "  2. Generate auth key"
  echo "  3. Reusable: ✅  |  Expiration: 90 days  |  Tags: (пусто)"
  echo ""
  read -rp "Вставьте auth key: " TS_KEY
  [ -z "$TS_KEY" ] && { err "Ключ не может быть пустым"; exit 1; }
  sed -i "s|TS_AUTHKEY=.*|TS_AUTHKEY=${TS_KEY}|" .env
  ok "Ключ сохранён в .env"
else
  ok "Auth key уже задан"
fi

# --- Start containers ---
header "Запуск контейнеров"

info "tailscale + mediamtx..."
$COMPOSE up -d tailscale mediamtx
ok "Контейнеры запущены"

# --- Wait for Tailscale ---
header "Ожидание подключения Tailscale"

for i in $(seq 1 20); do
  if $COMPOSE exec -T tailscale tailscale status >/dev/null 2>&1; then
    break
  fi
  sleep 1
  printf "."
done
echo ""

TS_IP=$($COMPOSE exec -T tailscale tailscale ip -4 2>/dev/null | tr -d '[:space:]') || true
if [ -n "$TS_IP" ]; then
  ok "Tailscale подключён: ${BOLD}${TS_IP}${NC}"
else
  err "Tailscale ещё не подключился. Проверьте: docker compose logs tailscale"
fi

# --- Firewall ---
header "Firewall (RTSP 8554)"

if command -v iptables >/dev/null 2>&1; then
  # Remove old rules if re-running
  sudo iptables -D INPUT -p tcp --dport 8554 -i tailscale0 -j ACCEPT 2>/dev/null || true
  sudo iptables -D INPUT -p tcp --dport 8554 -j DROP 2>/dev/null || true
  # Add new rules
  sudo iptables -I INPUT -p tcp --dport 8554 -i tailscale0 -j ACCEPT
  sudo iptables -A INPUT -p tcp --dport 8554 -j DROP
  ok "Порт 8554 открыт только через Tailscale"

  # Persist with iptables-save if available
  if command -v iptables-save >/dev/null 2>&1 && [ -d /etc/iptables ]; then
    sudo iptables-save | sudo tee /etc/iptables/rules.v4 >/dev/null 2>&1 || true
  fi
else
  info "iptables не найден — закройте порт 8554 на публичном интерфейсе вручную"
fi

# --- Summary ---
header "Готово!"
echo ""
echo -e "Tailscale IP VPS: ${BOLD}${GREEN}${TS_IP:-???}${NC}"
echo ""
echo "Камеры в VLC:"
echo -e "  ${CYAN}rtsp://${TS_IP:-<tailscale-ip>}:8554/cam1${NC}  ← канал /10"
echo -e "  ${CYAN}rtsp://${TS_IP:-<tailscale-ip>}:8554/cam2${NC}  ← канал /20"
echo ""
echo -e "${BOLD}Осталось настроить Keenetic:${NC}"
echo "  1. Роутер → Управление → Общие настройки → Обновления → Компоненты"
echo "     → Установить «Tailscale»"
echo "  2. Другие подключения → Tailscale → Войти"
echo "  3. SSH в роутер (или CLI в веб-интерфейсе):"
echo -e "     ${CYAN}tailscale up --advertise-routes=192.168.1.0/24 --accept-routes${NC}"
echo "  4. Tailscale Admin Console → Machines → Keenetic → ⋯ → Edit route settings"
echo "     → Approve 192.168.1.0/24"
echo ""
