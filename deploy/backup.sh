#!/usr/bin/env bash
# 弈星纪元兽装工作室官网 — 备份与恢复演练脚本
#
# 规则（14 文档）：
# - SQLite WAL 活跃时禁止只复制 .db 文件作为备份；
# - 备份使用 SQLite online backup API 生成一致快照，做完整性检查后加密上传独立存储；
# - 备份数据库的同时备份对象存储清单、schema 版本与必要配置引用；
# - 恢复后必须对恢复时间之后的全部渠道交易重新对账，确认无遗漏后才逐步开单。
#
# 用法：
#   deploy/backup.sh backup                 生成快照
#   deploy/backup.sh verify <snapshot>      校验快照（隔离目录）
#   deploy/backup.sh restore <snapshot> <目标目录>
#   deploy/backup.sh drill                  备份 + 恢复到隔离目录 + 报告 RPO/RTO

set -Eeuo pipefail

APP_DIR="${STARIUM_APP_DIR:-/opt/starium/server}"
PY="${APP_DIR}/.venv/bin/python"
DATA_DIR="${STARIUM_DATA_DIR:-/var/lib/starium}"
BACKUP_DIR="${STARIUM_BACKUP_DIR:-/var/backups/starium}"
LOG_DIR="${STARIUM_LOG_DIR:-/var/log/starium}"

log() { printf '%s [backup] %s\n' "$(date -Is)" "$*"; }
die() { printf '%s [backup][ERROR] %s\n' "$(date -Is)" "$*" >&2; exit 1; }

require_env() {
  [[ -n "${BACKUP_TARGET:-}" ]] || die "BACKUP_TARGET 未配置：离机备份目标必须与主机账户权限分离"
  [[ -n "${BACKUP_KEY:-}" ]] || die "BACKUP_KEY 未配置：备份密钥不能只保存在本机"
}

cmd_backup() {
  require_env
  mkdir -p "${BACKUP_DIR}"
  cd "${APP_DIR}"
  local stamp label
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  label="${1:-scheduled}"
  log "生成一致性快照（label=${label}, stamp=${stamp}）"
  "${PY}" -m app.cli backup --label "${stamp}-${label}"
  local snapshot
  snapshot="$(ls -1t "${BACKUP_DIR}"/*"${stamp}-${label}"* 2>/dev/null | head -n1 || true)"
  [[ -n "${snapshot}" ]] || die "未找到刚生成的快照，检查 DATA_DIR 权限"

  log "校验快照"
  "${PY}" -m app.cli restore --path "${snapshot}" > "${LOG_DIR}/verify-${stamp}.json" \
    || die "快照校验失败，禁止上传"

  log "加密并上传到离机目标"
  openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_KEY \
    -in "${snapshot}" -out "${snapshot}.enc"
  case "${BACKUP_TARGET}" in
    s3://*) aws s3 cp "${snapshot}.enc" "${BACKUP_TARGET%/}/$(basename "${snapshot}.enc")" ;;
    rsync://*|ssh://*) rsync -az --partial "${snapshot}.enc" "${BACKUP_TARGET%/}/" ;;
    /*) cp -f "${snapshot}.enc" "${BACKUP_TARGET%/}/" ;;
    *) die "不支持的 BACKUP_TARGET 形式：${BACKUP_TARGET}" ;;
  esac
  rm -f "${snapshot}.enc"
  log "备份完成：$(basename "${snapshot}")"

  # 保留期由 C22 定稿；此处只做工程默认分层，自动到期销毁。
  find "${BACKUP_DIR}" -type f -mtime +90 -name '*.snapshot*' -delete
}

cmd_verify() {
  local snapshot="${1:?用法: backup.sh verify <snapshot>}"
  cd "${APP_DIR}"
  "${PY}" -m app.cli restore --path "${snapshot}"
}

cmd_restore() {
  local snapshot="${1:?用法: backup.sh restore <snapshot> <目标目录>}"
  local target="${2:?用法: backup.sh restore <snapshot> <目标目录>}"
  cd "${APP_DIR}"
  log "停写与保留回调接收由运维在网关层执行；本步骤只做隔离恢复"
  "${PY}" - <<PY
from pathlib import Path
from app.ops.backup import restore_to
report = restore_to(Path("${snapshot}"), Path("${target}"))
print(report)
if not report.get("ok"):
    raise SystemExit("恢复校验失败，禁止切换入口")
PY
  log "恢复完成。下一步必须："
  log "  1) 应用注销/删除墓碑，防止已删除的私密资料复活"
  log "  2) 查询备份时间之后的全部渠道收款/退款与账单，幂等补账"
  log "  3) 核查库存、名额与积分，再切换入口并逐步开单"
}

cmd_drill() {
  local started ended target
  started="$(date +%s)"
  cmd_backup drill
  target="${BACKUP_DIR}/drill-rose-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "${target}"
  local snapshot
  snapshot="$(ls -1t "${BACKUP_DIR}"/*drill* | head -n1)"
  cmd_restore "${snapshot}" "${target}"
  ended="$(date +%s)"
  log "演练用时 $((ended - started)) 秒（RTO 目标 ≤ 2 小时；RPO 目标 ≤ 15 分钟）"
  log "演练报告写入 ${LOG_DIR}/drill-$(date -u +%Y%m%d).log"
}

case "${1:-backup}" in
  backup) shift || true; cmd_backup "$@" ;;
  verify) shift; cmd_verify "$@" ;;
  restore) shift; cmd_restore "$@" ;;
  drill) shift || true; cmd_drill ;;
  *) die "未知子命令：$1（可用：backup | verify | restore | drill）" ;;
esac
