#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# install.sh — автоматическая установка herness на сервер
# ══════════════════════════════════════════════════════════════════════
# Использование:
#   curl -fsSL https://raw.githubusercontent.com/gubarandrej-bit/herness/main/install.sh | bash
# или
#   wget -qO- https://raw.githubusercontent.com/gubarandrej-bit/herness/main/install.sh | bash
# ══════════════════════════════════════════════════════════════════════

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}==>${NC} $1"; }
ok()   { echo -e " ${GREEN}✓${NC} $1"; }
warn() { echo -e " ${YELLOW}⚠${NC} $1"; }
err()  { echo -e " ${RED}✗${NC} $1"; exit 1; }

HERNESS_DIR="${HOME}/herness"

# --- проверка Docker -------------------------------------------------
check_prereqs() {
  log "Проверка системных требований..."

  if command -v docker &>/dev/null; then
    ok "Docker установлен: $(docker --version)"
  else
    err "Docker не найден. Установите Docker: https://docs.docker.com/engine/install/"
  fi

  if command -v docker compose &>/dev/null; then
    ok "Docker Compose установлен"
  elif docker compose version &>/dev/null; then
    ok "Docker Compose (плагин) установлен"
  else
    err "Docker Compose не найден. Установите: https://docs.docker.com/compose/install/"
  fi

  # Проверка архитектуры и ОС
  ARCH=$(uname -m)
  OS=$(uname -s)
  log "Платформа: ${OS} / ${ARCH}"

  # Рекомендуемые минимальные требования
  TOTAL_RAM_GB=$(awk '/^MemTotal:/{printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)
  if [ "$TOTAL_RAM_GB" -gt 0 -a "$TOTAL_RAM_GB" -lt 2 ]; then
    warn "Оперативной памяти: ${TOTAL_RAM_GB} ГБ. Рекомендуется 4+ ГБ (без локальной ИИ) или 8+ ГБ (с Ollama)"
  elif [ "$TOTAL_RAM_GB" -gt 0 ]; then
    ok "Оперативной памяти: ${TOTAL_RAM_GB} ГБ"
  fi
}

# --- клонирование репозитория -----------------------------------------
clone_repo() {
  log "Клонирование репозитория herness..."

  if [ -d "$HERNESS_DIR" ]; then
    warn "Каталог ${HERNESS_DIR} уже существует. Обновляю..."
    cd "$HERNESS_DIR"
    git pull origin main
  else
    git clone https://github.com/gubarandrej-bit/herness.git "$HERNESS_DIR"
    cd "$HERNESS_DIR"
  fi
  ok "Репозиторий: $(git rev-parse --short HEAD)"
}

# --- сборка и запуск --------------------------------------------------
build_and_run() {
  log "Сборка образа приложения..."
  docker compose build

  log "Запуск контейнеров..."
  docker compose up -d

  ok "Приложение запущено"
}

# --- проверка работоспособности ---------------------------------------
check_health() {
  log "Проверка работоспособности..."
  sleep 5

  for i in $(seq 1 12); do
    if curl -fsS "http://127.0.0.1:8080/health" &>/dev/null; then
      ok "Сервер отвечает на порту 8080"
      return 0
    fi
    sleep 5
  done

  warn "Сервер не ответил за 60 секунд. Проверьте логи: docker compose logs app"
  return 1
}

# --- настройка Ollama (опционально) ------------------------------------
setup_ollama() {
  echo ""
  warn "═══════════════════════════════════════════════════════════════"
  warn "Для работы локальной ИИ-модели требуется настроить Ollama."
  warn "Это займёт ~10-30 минут (скачивание модели)."
  warn "═══════════════════════════════════════════════════════════════"
  read -r -p "Запустить локальную ИИ-модель (Ollama/qwen2.5)? [y/N] " REPLY
  if [[ ! "$REPLY" =~ ^[YyДд] ]]; then
    warn "Локальная ИИ пропущена. Для работы в облачном режиме добавьте модель через админку."
    return
  fi

  log "Запуск Ollama..."
  docker compose --profile local-ai up -d

  sleep 10

  if docker compose exec ollama ollama list &>/dev/null; then
    log "Скачивание модели qwen2.5:7b-instruct (~4-5 ГБ)..."
    docker compose exec ollama ollama pull qwen2.5:7b-instruct &
    download_pid=$!

    while kill -0 $download_pid 2>/dev/null; do
      echo -n "."
      sleep 10
    done

    wait $download_pid
    ok "Модель qwen2.5:7b-instruct загружена"
  else
    warn "Ollama запущен, но не отвечает. Проверьте логи: docker compose logs ollama"
  fi
}

# --- финальная информация ----------------------------------------------
show_info() {
  echo ""
  echo "═══════════════════════════════════════════════════════════════"
  echo "  ⚡ herness — установка завершена"
  echo "═══════════════════════════════════════════════════════════════"
  echo ""
  echo "  Адрес:        http://$(curl -s ifconfig.me 2>/dev/null || echo 'localhost'):8080"
  echo "  Локально:     http://127.0.0.1:8080"
  echo "  Логин:        admin"
  echo "  Пароль:       admin123"
  echo ""
  echo "  Управление:   docker compose -f ${HERNESS_DIR}/docker-compose.yml"
  echo "  Логи:         docker compose logs -f app"
  echo "  Остановка:    docker compose down"
  echo "  Обновление:   git pull && docker compose up -d --build"
  echo ""
  echo "  📄 Документация: https://github.com/gubarandrej-bit/herness"
  echo "═══════════════════════════════════════════════════════════════"
}

# --- главная -----------------------------------------------------------
main() {
  echo "╔══════════════════════════════════════════════════════════════╗"
  echo "║              ⚡ herness — установка                         ║"
  echo "║  Система анализа и проверки рабочей документации           ║"
  echo "╚══════════════════════════════════════════════════════════════╝"
  echo ""

  check_prereqs
  clone_repo
  build_and_run
  check_health
  setup_ollama
  show_info
}

main "$@"