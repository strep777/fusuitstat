#!/usr/bin/env bash
# 弈星纪元兽装工作室官网 — 发布脚本
#
# 流程（14 文档「发布流程」）：
# 1) CI 从锁文件安装，lint/类型检查/测试/依赖与密钥扫描，产出前端静态包与后端包；
# 2) 预发布执行迁移、烟雾测试、支付回调与 worker 恢复测试；
# 3) 生产发布前确认变更窗口、值班负责人、可恢复备份、迁移兼容性与回退条件；
# 4) 单 SQLite 主机使用兼容迁移 + 短暂停写/滚动应用进程；数据库迁移只运行一次；
# 5) 静态资源先上传带内容哈希的文件，校验成功后原子切换入口；
# 6) 观察错误率、支付未知单、锁等待、对账差异与短信费用；超门槛关闭新下单入口，
#    但保留查单、售后与回调接收能力。

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="${ROOT}/web"
SERVER_DIR="${ROOT}/server"
RELEASE_DIR="${STARIUM_RELEASE_DIR:-/var/www/starium}"
SERVICE_USER="${STARIUM_USER:-starium}"

log() { printf '%s [release] %s\n' "$(date -Is)" "$*"; }
die() { printf '%s [release][ERROR] %s\n' "$(date -Is)" "$*" >&2; exit 1; }

stage_build() {
  log "前端构建（类型检查 + 两个入口）"
  (cd "${WEB_DIR}" && pnpm install --frozen-lockfile && pnpm build)

  log "导出 OpenAPI 契约"
  (cd "${SERVER_DIR}" && uv run python -m app.cli export-openapi --output ../contracts/openapi.json)

  log "后端质量门禁"
  (cd "${SERVER_DIR}" && uv run ruff check app tests && uv run pytest -q)
}

stage_migrate() {
  log "备份当前数据库"
  "${ROOT}/deploy/backup.sh" backup premigrate

  log "检查配置（不安全组合会 fail closed）"
  (cd "${SERVER_DIR}" && uv run python -m app.cli check-config)

  log "应用迁移（只运行一次，禁止两个不兼容版本同时写库）"
  (cd "${SERVER_DIR}" && uv run alembic upgrade head)

  log "迁移后一致性检查"
  (cd "${SERVER_DIR}" && uv run python -m app.cli integrity)
}

stage_switch() {
  local release_name="$1"
  local target="${RELEASE_DIR}/releases/${release_name}"
  local current="${RELEASE_DIR}/current"

  log "上传静态资源到 ${target}"
  mkdir -p "${target}"
  cp -r "${WEB_DIR}/dist/." "${target}/"
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${target}"

  log "校验入口文件存在"
  [[ -f "${target}/index.html" ]] || die "缺少 index.html，禁止切换"
  [[ -f "${target}/admin.html" ]] || die "缺少 admin.html，禁止切换"

  log "原子切换 current 软链"
  ln -sfn "${target}" "${current}.new"
  mv -Tf "${current}.new" "${current}"

  log "重启应用与 worker"
  systemctl restart starium-api.service
  systemctl restart starium-worker.service

  log "等待就绪"
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/readyz >/dev/null; then
      log "服务已就绪"
      return 0
    fi
    sleep 1
  done
  die "服务未在 30 秒内就绪，按 14 文档执行回滚（保留新交易，优先正向修复）"
}

usage() {
  cat <<'USAGE'
用法：
  deploy/release.sh build              构建与质量门禁
  deploy/release.sh migrate            备份 + 配置检查 + 迁移 + 一致性检查
  deploy/release.sh switch <release>   上传静态包并原子切换 + 重启服务
  deploy/release.sh all <release>      build + migrate + switch
USAGE
}

case "${1:-}" in
  build) stage_build ;;
  migrate) stage_migrate ;;
  switch) shift; stage_switch "${1:?缺少 release 名称}" ;;
  all) shift; stage_build; stage_migrate; stage_switch "${1:?缺少 release 名称}" ;;
  *) usage; exit 1 ;;
esac
