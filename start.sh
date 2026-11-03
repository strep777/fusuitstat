#!/usr/bin/env bash
# =============================================================================
# 弈星纪元兽装工作室官网 · 本地开发一键启动（Linux / macOS / Git Bash）
#
# 用法：
#   ./start.sh               启动后端 :8000 与前端 :5173，就绪后打开浏览器
#   ./start.sh --no-browser  启动但不打开浏览器
#   ./start.sh --install     强制重装依赖（uv sync / pnpm install）
#   ./start.sh stop          停止占用 8000 / 5173 的开发进程
#   ./start.sh --help        查看帮助
#
# 端口固定，不要单独改：
#   web/vite.config.ts 把 /api 代理到 127.0.0.1:8000；
#   后端开发环境 CORS 只放行 http://127.0.0.1:5173 与 http://localhost:5173。
#
# 日志：.logs/backend.log、.logs/frontend.log
# Windows 用户请优先使用 start.bat。
# =============================================================================

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$ROOT/server"
WEB_DIR="$ROOT/web"
LOG_DIR="$ROOT/.logs"
WAIT_TIMEOUT="${STARIUM_WAIT:-120}"

API_PORT=8000
WEB_PORT=5173
API_URL="http://127.0.0.1:${API_PORT}"
WEB_URL="http://127.0.0.1:${WEB_PORT}/"
ADMIN_URL="http://127.0.0.1:${WEB_PORT}/admin.html"

OPEN_BROWSER=1
FORCE_INSTALL=0
ACTION=start
RELOAD=1

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) IS_WINDOWS=1 ;;
  *) IS_WINDOWS=0 ;;
esac

if [[ -t 1 ]] && [[ "${NO_COLOR:-}" == "" ]]; then
  C_RESET=$'\033[0m' C_DIM=$'\033[2m' C_RED=$'\033[31m'
  C_GREEN=$'\033[32m' C_YELLOW=$'\033[33m' C_CYAN=$'\033[36m'
else
  C_RESET="" C_DIM="" C_RED="" C_GREEN="" C_YELLOW="" C_CYAN=""
fi

info() { printf '%s[start]%s %s\n' "$C_CYAN" "$C_RESET" "$*"; }
ok() { printf '%s[ ok ]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die() { printf '%s[fail]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# 去重但不用 sort：Git Bash 可能把 sort 解析到 Windows 自带的 sort.exe（不支持 -u）。
dedupe() { awk '!seen[$0]++'; }

usage() {
  cat <<'EOF'
用法：
  ./start.sh               启动后端 :8000 与前端 :5173，就绪后打开浏览器
  ./start.sh --no-browser  启动但不打开浏览器
  ./start.sh --install     强制重装依赖（uv sync / pnpm install）
  ./start.sh --no-reload   关闭后端热重载（默认开启）
  ./start.sh stop          停止占用 8000 / 5173 的开发进程
  ./start.sh --help        查看帮助

端口固定，不要单独改：前端把 /api 代理到 127.0.0.1:8000，后端开发环境
CORS 只放行 http://127.0.0.1:5173 与 http://localhost:5173。
日志：.logs/backend.log、.logs/frontend.log
EOF
}

# ---------------------------------------------------------------------------
# 环境探测
# ---------------------------------------------------------------------------
VENV_PY=""
if [[ -x "$SERVER_DIR/.venv/Scripts/python.exe" ]]; then
  VENV_PY="$SERVER_DIR/.venv/Scripts/python.exe"
elif [[ -x "$SERVER_DIR/.venv/bin/python" ]]; then
  VENV_PY="$SERVER_DIR/.venv/bin/python"
fi

# ---------------------------------------------------------------------------
# HTTP 工具（curl 优先，wget 兜底）
# ---------------------------------------------------------------------------
http_ok() {
  local url=$1
  if have curl; then
    curl -fsS --max-time 3 -o /dev/null "$url" >/dev/null 2>&1
  elif have wget; then
    wget -q -T 3 -O /dev/null "$url" >/dev/null 2>&1
  else
    return 1
  fi
}

# 端口上的服务是不是我们自己的前端（用来避免误杀/误用别的程序）。
web_is_starium() {
  local body
  if have curl; then
    body="$(curl -fsS --max-time 3 "$WEB_URL" 2>/dev/null)" || return 1
  elif have wget; then
    body="$(wget -q -T 3 -O - "$WEB_URL" 2>/dev/null)" || return 1
  else
    return 1
  fi
  [[ $body == *site-main.ts* ]]
}

wait_for() { # $1=描述 $2=检查函数名 $3=超时秒数
  local label=$1 check=$2 timeout=${3:-120} i=0
  while ((i < timeout)); do
    if "$check"; then return 0; fi
    sleep 1
    i=$((i + 1))
    if ((i % 15 == 0)); then info "${label} 还在启动…（${i}s）"; fi
  done
  return 1
}

api_ready() { http_ok "${API_URL}/readyz"; }

# ---------------------------------------------------------------------------
# 端口与进程
# ---------------------------------------------------------------------------
port_pids() {
  local port=$1
  if [[ $IS_WINDOWS -eq 1 ]]; then
    # 注意：Git Bash 里 sort/find 可能先解析到 Windows 自带同名工具（不支持 -u），
    # 所以去重统一用 awk，不调用 sort。
    netstat -ano -p tcp 2>/dev/null |
      tr -d '\r' |
      awk -v p=":${port}" '$2 ~ p "$" && $4 == "LISTENING" { print $5 }' |
      dedupe
  elif have lsof; then
    lsof -nP -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null | dedupe
  elif have ss; then
    ss -ltnp "sport = :${port}" 2>/dev/null |
      sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | dedupe
  elif have fuser; then
    fuser "${port}/tcp" 2>/dev/null | tr -s ' ' '\n' | sed '/^$/d' | dedupe
  fi
}

stop_pid() {
  local pid=$1
  [[ -z $pid ]] && return 0
  if [[ $IS_WINDOWS -eq 1 ]]; then
    taskkill //F //T //PID "$pid" >/dev/null 2>&1 || true
  else
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      kill -0 "$pid" 2>/dev/null || return 0
      sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
}

# ---------------------------------------------------------------------------
# 依赖安装
# ---------------------------------------------------------------------------
ensure_backend_deps() {
  if [[ -n $VENV_PY ]] && [[ $FORCE_INSTALL -eq 0 ]]; then
    ok "后端虚拟环境已存在：server/.venv"
    return 0
  fi
  have uv || die "未找到 uv。请先安装 uv（https://docs.astral.sh/uv/）后重试。"
  export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.uv-cache}"
  info "安装后端依赖：uv sync"
  (cd "$SERVER_DIR" && uv sync) || die "uv sync 失败，请检查网络或 uv 配置。"
  if [[ -x "$SERVER_DIR/.venv/Scripts/python.exe" ]]; then
    VENV_PY="$SERVER_DIR/.venv/Scripts/python.exe"
  else
    VENV_PY="$SERVER_DIR/.venv/bin/python"
  fi
  ok "后端依赖就绪"
}

ensure_frontend_deps() {
  if [[ $FORCE_INSTALL -eq 0 ]] && [[ -f "$WEB_DIR/node_modules/vite/bin/vite.js" ]]; then
    ok "前端依赖已存在：web/node_modules"
    return 0
  fi
  have node || die "未找到 Node.js。请安装 Node.js >= 20.19.0 后重试。"
  have pnpm || die "未找到 pnpm。请执行 npm i -g pnpm 后重试。"
  info "安装前端依赖：pnpm install"
  if [[ -f "$WEB_DIR/pnpm-lock.yaml" ]]; then
    (cd "$WEB_DIR" && pnpm install --frozen-lockfile) || die "pnpm install 失败，请检查网络或 lockfile。"
  else
    (cd "$WEB_DIR" && pnpm install) || die "pnpm install 失败，请检查网络。"
  fi
  ok "前端依赖就绪"
}

# 数据库缺失（首次 clone）时初始化并写入演示数据；已有库不重新初始化。
ensure_database() {
  local db_file="$SERVER_DIR/var/starium.db"
  if [[ -f $db_file ]]; then
    ok "数据库已存在：server/var/starium.db"
    return 0
  fi
  info "首次运行：初始化数据库（alembic upgrade head）"
  (cd "$SERVER_DIR" && "$VENV_PY" -m app.cli init-db) || die "数据库初始化失败，见上方日志。"
  info "写入演示数据（幂等）"
  (cd "$SERVER_DIR" && "$VENV_PY" -m app.cli seed-demo) ||
    warn "seed-demo 未成功，页面可能缺少演示数据；可稍后手动执行。"
  ok "数据库就绪"
}

# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
API_PID=""
WEB_PID=""
API_REUSED=0
WEB_REUSED=0

cleanup() {
  local code=$?
  trap - EXIT INT TERM
  if [[ -n $WEB_PID ]] && kill -0 "$WEB_PID" 2>/dev/null; then
    info "停止前端（PID ${WEB_PID}）"
    stop_pid "$WEB_PID"
  fi
  if [[ -n $API_PID ]] && kill -0 "$API_PID" 2>/dev/null; then
    info "停止后端（PID ${API_PID}）"
    stop_pid "$API_PID"
  fi
  if [[ -n $API_PID || -n $WEB_PID ]] &&
    { [[ $WEB_REUSED -eq 1 ]] || [[ $API_REUSED -eq 1 ]]; }; then
    warn "复用的旧进程未被停止，如需清理请执行：./start.sh stop"
  fi
  exit "$code"
}

open_browser() {
  local url=$1
  if [[ $IS_WINDOWS -eq 1 ]]; then
    cmd.exe /c start "" "$url" >/dev/null 2>&1 || true
  elif have open; then
    open "$url" >/dev/null 2>&1 || true
  elif have xdg-open; then
    xdg-open "$url" >/dev/null 2>&1 || true
  else
    warn "未找到可用的浏览器打开命令，请手动访问：$url"
  fi
}

start_dev() {
  mkdir -p "$LOG_DIR"

  info "检查运行环境"
  have node || die "未找到 Node.js。请安装 Node.js >= 20.19.0 后重试。"

  ensure_backend_deps
  ensure_database
  ensure_frontend_deps

  if [[ -n $(port_pids "$API_PORT") ]]; then
    if http_ok "${API_URL}/readyz"; then
      API_REUSED=1
      warn "端口 ${API_PORT} 已有可用的本站在运行，复用该后端进程。"
    else
      die "端口 ${API_PORT} 已被占用，但不是可用的后端（/readyz 不通）。请先执行：./start.sh stop"
    fi
  else
    info "启动后端：http://127.0.0.1:${API_PORT}（日志 .logs/backend.log）"
    reload_args=()
    if [[ $RELOAD -eq 1 ]]; then reload_args=(--reload); fi
    (
      cd "$SERVER_DIR"
      exec "$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" \
        "${reload_args[@]+"${reload_args[@]}"}"
    ) >>"$LOG_DIR/backend.log" 2>&1 &
    API_PID=$!
  fi

  if [[ -n $(port_pids "$WEB_PORT") ]]; then
    if web_is_starium; then
      WEB_REUSED=1
      warn "端口 ${WEB_PORT} 已有本站在运行，复用该前端进程。"
    else
      die "端口 ${WEB_PORT} 已被其他程序占用。请先执行：./start.sh stop"
    fi
  else
    info "启动前端：http://127.0.0.1:${WEB_PORT}（日志 .logs/frontend.log）"
    (
      cd "$WEB_DIR"
      exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port "$WEB_PORT" --strictPort
    ) >>"$LOG_DIR/frontend.log" 2>&1 &
    WEB_PID=$!
  fi

  info "等待后端就绪（/readyz）"
  if ! wait_for "后端" api_ready "$WAIT_TIMEOUT"; then
    warn "后端 ${WAIT_TIMEOUT} 秒内未就绪，最后 20 行日志："
    tail -n 20 "$LOG_DIR/backend.log" >&2 || true
    exit 1
  fi
  ok "后端就绪：${API_URL}/api/docs"

  info "等待前端就绪（${WEB_URL}）"
  if ! wait_for "前端" web_is_starium "$WAIT_TIMEOUT"; then
    warn "前端 ${WAIT_TIMEOUT} 秒内未就绪，最后 20 行日志："
    tail -n 20 "$LOG_DIR/frontend.log" >&2 || true
    exit 1
  fi
  ok "前端就绪"

  cat <<EOF

  官网首页    ${WEB_URL}
  后台入口    ${ADMIN_URL}
  API 文档    ${API_URL}/api/docs
  健康检查    ${API_URL}/healthz

  日志        .logs/backend.log  .logs/frontend.log
  停止        Ctrl-C，或另开终端执行 ./start.sh stop

EOF

  if [[ $OPEN_BROWSER -eq 1 ]]; then
    open_browser "$WEB_URL"
  fi

  if [[ -z $API_PID && -z $WEB_PID ]]; then
    info "两个服务都已在运行，本次没有启动新进程。停止请执行：./start.sh stop"
    exit 0
  fi

  info "服务运行中，按 Ctrl-C 停止。"
  # 前台等待自己拉起的进程：任一退出即整体退出（复用进程不会被这里等待）。
  wait
}

stop_dev() {
  local port pids pid found=0
  for port in "$WEB_PORT" "$API_PORT"; do
    pids="$(port_pids "$port" || true)"
    [[ -z $pids ]] && continue
    while read -r pid; do
      [[ -z $pid ]] && continue
      found=1
      info "停止端口 ${port} 上的进程 PID ${pid}"
      stop_pid "$pid"
    done <<<"$pids"
  done
  if [[ $found -eq 0 ]]; then
    ok "端口 ${WEB_PORT} / ${API_PORT} 上没有运行中的开发进程。"
  else
    ok "开发进程已停止。"
  fi
}

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    stop) ACTION=stop ;;
    --no-browser | --no-open) OPEN_BROWSER=0 ;;
    --install) FORCE_INSTALL=1 ;;
    --no-reload) RELOAD=0 ;;
    -h | --help) usage; exit 0 ;;
    *) usage; die "未知参数：$1" ;;
  esac
  shift
done

if ! command -v node >/dev/null 2>&1 && [[ $ACTION == start ]]; then
  die "未找到 Node.js。请安装 Node.js >= 20.19.0 后重试。"
fi

if [[ $ACTION == stop ]]; then
  stop_dev
  exit 0
fi

trap cleanup EXIT INT TERM
start_dev
