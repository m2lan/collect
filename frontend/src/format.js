export const STATUS = {
  upcoming: { label: '即将开始', type: 'info' },
  live: { label: '进行中', type: 'success' },
  ended: { label: '已结束', type: '' },
  unsold: { label: '流拍', type: 'warning' },
}

export const RUN_STATUS = {
  pending: { label: '排队中', type: 'info' },
  running: { label: '采集中', type: 'primary' },
  success: { label: '成功', type: 'success' },
  failed: { label: '失败', type: 'danger' },
}

export function fmtTime(v) {
  if (!v) return '-'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function fmtPrice(v, currency) {
  if (v === null || v === undefined) return '-'
  const n = Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  return currency && currency !== 'CNY' ? `${currency} ${n}` : `¥${n}`
}
