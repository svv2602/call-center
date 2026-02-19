#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# --- Colors & formatting ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

PIDS_DIR=".pids"
COMPOSE_FILE="docker-compose.dev.yml"

info()    { echo -e "${BLUE}▶${NC} $1"; }
success() { echo -e "  ${GREEN}✓${NC} $1"; }
error()   { echo -e "  ${RED}✗${NC} $1" >&2; }
warn()    { echo -e "  ${YELLOW}!${NC} $1"; }

# --- Helper functions ---

mkdir -p "$PIDS_DIR" logs

load_env() {
    if [[ -f .env.local ]]; then
        set -a; . ./.env.local; set +a
    else
        warn ".env.local не найден, переменные окружения не загружены"
    fi
}

wait_for_docker() {
    local timeout=30
    local elapsed=0
    info "Ожидание готовности сервисов..."
    while (( elapsed < timeout )); do
        local healthy
        healthy=$(docker compose -f "$COMPOSE_FILE" ps --format json 2>/dev/null \
            | jq -r 'select(.Health == "healthy") | .Service' 2>/dev/null \
            | wc -l)
        if (( healthy >= 3 )); then
            success "PostgreSQL готов"
            success "Redis готов"
            success "Store API готов"
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    error "Таймаут ожидания Docker сервисов (${timeout}с)"
    return 1
}

check_venv() {
    if [[ ! -d .venv ]] || [[ ! -f .venv/bin/python ]]; then
        error "Виртуальное окружение .venv не найдено"
        echo "  Создайте его: python3.12 -m venv .venv && pip install -e \".[dev,test]\""
        exit 1
    fi
}

check_node_modules() {
    if [[ ! -d admin-ui/node_modules ]]; then
        error "admin-ui/node_modules не найден"
        echo "  Установите зависимости: cd admin-ui && npm install"
        exit 1
    fi
}

# --- Port cleanup ---

# Ports used by components
PORT_BACKEND=8080
PORT_AUDIOSOCKET=9092
PORT_VITE=5173

free_port() {
    local port=$1
    local pids
    pids=$(lsof -ti :"$port" 2>/dev/null) || true
    if [[ -n "$pids" ]]; then
        warn "Порт $port занят (PID: $(echo $pids | tr '\n' ' ')), завершаю..."
        echo "$pids" | xargs kill 2>/dev/null || true
        sleep 1
        # Force kill survivors
        pids=$(lsof -ti :"$port" 2>/dev/null) || true
        if [[ -n "$pids" ]]; then
            echo "$pids" | xargs kill -9 2>/dev/null || true
            sleep 0.5
        fi
        success "Порт $port освобождён"
    fi
}

free_ports_for() {
    local mode=$1
    case "$mode" in
        dev)
            free_port "$PORT_BACKEND"
            free_port "$PORT_AUDIOSOCKET"
            free_port "$PORT_VITE"
            ;;
        backend|build)
            free_port "$PORT_BACKEND"
            free_port "$PORT_AUDIOSOCKET"
            ;;
    esac
}

# --- Component start/stop ---

start_docker() {
    info "Запуск Docker (PostgreSQL, Redis, Store API)..."
    docker compose -f "$COMPOSE_FILE" up -d --wait 2>/dev/null || {
        docker compose -f "$COMPOSE_FILE" up -d
        wait_for_docker
        return
    }
    success "Docker контейнеры запущены"
}

start_backend() {
    check_venv
    load_env
    info "Запуск Backend (Python)..."

    # Stop previous instance if running
    if [[ -f "$PIDS_DIR/backend.pid" ]]; then
        local old_pid
        old_pid=$(cat "$PIDS_DIR/backend.pid")
        if kill -0 "$old_pid" 2>/dev/null; then
            kill "$old_pid" 2>/dev/null || true
            sleep 1
        fi
    fi

    .venv/bin/python -m src.main &
    local pid=$!
    echo "$pid" > "$PIDS_DIR/backend.pid"
    success "Backend запущен (PID $pid)"
}

start_vite_dev() {
    check_node_modules
    info "Запуск Vite dev server..."

    # Stop previous instance if running
    if [[ -f "$PIDS_DIR/vite.pid" ]]; then
        local old_pid
        old_pid=$(cat "$PIDS_DIR/vite.pid")
        if kill -0 "$old_pid" 2>/dev/null; then
            kill "$old_pid" 2>/dev/null || true
            sleep 1
        fi
    fi

    (cd admin-ui && npx vite) &
    local pid=$!
    echo "$pid" > "$PIDS_DIR/vite.pid"
    success "Vite dev запущен (PID $pid)"
}

start_celery() {
    check_venv
    load_env
    info "Запуск Celery worker..."

    # Stop previous instance if running
    if [[ -f "$PIDS_DIR/celery.pid" ]]; then
        local old_pid
        old_pid=$(cat "$PIDS_DIR/celery.pid")
        if kill -0 "$old_pid" 2>/dev/null; then
            kill "$old_pid" 2>/dev/null || true
            sleep 2
        fi
    fi

    .venv/bin/celery -A src.tasks.celery_app worker \
        -Q celery,scraper -c 1 -n worker@%h \
        --loglevel=info > logs/celery-worker.log 2>&1 &
    local pid=$!
    echo "$pid" > "$PIDS_DIR/celery.pid"
    success "Celery worker запущен (PID $pid, лог: logs/celery-worker.log)"
}

build_admin_ui() {
    check_node_modules
    info "Сборка Admin UI (Vite build)..."
    (cd admin-ui && npx vite build)
    success "Admin UI собран в admin-ui/dist/"
}

stop_process() {
    local name=$1
    local pidfile="$PIDS_DIR/${name}.pid"
    if [[ -f "$pidfile" ]]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            # Wait up to 5s for graceful shutdown
            local i=0
            while (( i < 10 )) && kill -0 "$pid" 2>/dev/null; do
                sleep 0.5
                i=$((i + 1))
            done
            # Force kill if still alive
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
            success "$name остановлен (PID $pid)"
        else
            warn "$name уже не запущен (PID $pid)"
        fi
        rm -f "$pidfile"
    fi
}

stop_all() {
    info "Остановка всех компонентов..."
    stop_process "vite"
    stop_process "celery"
    stop_process "backend"

    if docker compose -f "$COMPOSE_FILE" ps --quiet 2>/dev/null | grep -q .; then
        info "Остановка Docker контейнеров..."
        docker compose -f "$COMPOSE_FILE" down
        success "Docker контейнеры остановлены"
    else
        warn "Docker контейнеры уже остановлены"
    fi
}

# --- Trap for Ctrl+C ---

cleanup() {
    echo ""
    warn "Получен сигнал завершения, останавливаю процессы..."
    stop_process "vite"
    stop_process "celery"
    stop_process "backend"
    exit 0
}

trap cleanup SIGINT SIGTERM

# --- Output ---

show_urls() {
    local mode=$1
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    case "$mode" in
        dev)
            echo -e "  Admin UI:   ${GREEN}http://localhost:5173${NC}"
            echo -e "  Backend:    ${GREEN}http://localhost:8080${NC}"
            echo -e "  Store API:  ${GREEN}http://localhost:3000${NC}"
            ;;
        backend|build)
            echo -e "  Backend:    ${GREEN}http://localhost:8080${NC}"
            if [[ "$mode" == "build" ]]; then
                echo -e "  Admin UI:   ${GREEN}http://localhost:8080/admin${NC}"
            fi
            echo -e "  Store API:  ${GREEN}http://localhost:3000${NC}"
            ;;
        docker)
            echo -e "  PostgreSQL: ${GREEN}localhost:5432${NC}"
            echo -e "  Redis:      ${GREEN}localhost:6379${NC}"
            echo -e "  Store API:  ${GREEN}http://localhost:3000${NC}"
            ;;
    esac
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    if [[ "$mode" != "docker" ]]; then
        echo -e "  ${YELLOW}Ctrl+C${NC} для остановки"
    fi
}

show_usage() {
    echo -e "${BOLD}Call Center AI — скрипт запуска${NC}"
    echo ""
    echo "Использование: ./start.sh <команда>"
    echo ""
    echo "Команды:"
    echo "  dev       Docker + Backend + Celery + Vite dev (разработка фронта, HMR)"
    echo "  backend   Docker + Backend + Celery (разработка бэкенда)"
    echo "  docker    Только Docker (инфраструктура)"
    echo "  build     Docker + Vite build + Backend + Celery (продакшн-подобный)"
    echo "  stop      Остановка всех компонентов"
}

wait_for_children() {
    # Wait for background processes to keep the script alive for Ctrl+C
    wait
}

# --- Main ---

MODE="${1:-}"

if [[ -z "$MODE" ]]; then
    show_usage
    exit 0
fi

echo -e "${BOLD}Call Center AI — режим: ${BLUE}${MODE}${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

case "$MODE" in
    dev)
        free_ports_for dev
        start_docker
        start_backend
        start_celery
        start_vite_dev
        show_urls dev
        wait_for_children
        ;;
    backend)
        free_ports_for backend
        start_docker
        start_backend
        start_celery
        show_urls backend
        wait_for_children
        ;;
    docker)
        start_docker
        show_urls docker
        ;;
    build)
        free_ports_for build
        start_docker
        build_admin_ui
        start_backend
        start_celery
        show_urls build
        wait_for_children
        ;;
    stop)
        stop_all
        ;;
    *)
        error "Неизвестная команда: $MODE"
        echo ""
        show_usage
        exit 1
        ;;
esac
