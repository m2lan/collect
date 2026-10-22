#!/bin/sh
# 数据库定时备份（运行在 postgres 镜像中，使用其自带的 pg_dump）。
# 环境变量：PGHOST PGUSER PGPASSWORD PGDATABASE
#   BACKUP_DIR（默认 /backups）BACKUP_TIME（每天几点备份，默认 03:30）BACKUP_KEEP_DAYS（保留天数，默认 14）
#   RUN_ONCE=1 时只备份一次后退出（用于手动执行或测试）
# 在 BACKUP_DIR 下创建 .request 文件可触发立即备份（管理后台的“立即备份”按钮即如此）。
set -u
BACKUP_DIR=${BACKUP_DIR:-/backups}
BACKUP_TIME=${BACKUP_TIME:-03:30}
BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
mkdir -p "$BACKUP_DIR"

backup() {
  file="$BACKUP_DIR/collect_$(date +%Y%m%d_%H%M%S).sql.gz"
  started=$(date +%s)
  if pg_dump --no-owner --no-privileges | gzip > "$file.tmp" && [ -s "$file.tmp" ]; then
    mv "$file.tmp" "$file"
    echo "{\"ok\": true, \"file\": \"$(basename "$file")\", \"at\": \"$(date -Iseconds)\", \"seconds\": $(( $(date +%s) - started ))}" > "$BACKUP_DIR/.status.json"
    echo "backup ok: $file"
  else
    rm -f "$file.tmp"
    echo "{\"ok\": false, \"at\": \"$(date -Iseconds)\", \"error\": \"pg_dump 失败，请查看 backup 容器日志\"}" > "$BACKUP_DIR/.status.json"
    echo "backup failed" >&2
  fi
  find "$BACKUP_DIR" -name 'collect_*.sql.gz' -mtime +"$BACKUP_KEEP_DAYS" -delete
}

if [ "${RUN_ONCE:-0}" = "1" ]; then
  backup
  exit 0
fi

echo "backup service started: daily at $BACKUP_TIME, keep $BACKUP_KEEP_DAYS days, dir $BACKUP_DIR"
last=""
while true; do
  today=$(date +%F)
  if [ -f "$BACKUP_DIR/.request" ]; then
    rm -f "$BACKUP_DIR/.request"
    backup
  elif [ "$(date +%H:%M)" = "$BACKUP_TIME" ] && [ "$last" != "$today" ]; then
    last=$today
    backup
  fi
  sleep 15
done
