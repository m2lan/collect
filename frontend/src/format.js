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

export const HEALTH = {
  ok: { label: '正常', type: 'success' },
  warning: { label: '异常', type: 'warning' },
  broken: { label: '失效', type: 'danger' },
}

export const ERROR_TYPE = {
  login: '登录失效', blocked: '被拦截 / 风控', timeout: '超时', network: '网络 / 代理', structure: '页面结构变化', other: '其他',
}

export const pct = (v, digits = 1) => (v === null || v === undefined ? '-' : `${(v * 100).toFixed(digits)}%`)

export function fmtNum(v, digits = 0) {
  if (v === null || v === undefined) return '-'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

export function fmtDuration(ms) {
  if (!ms && ms !== 0) return '-'
  return ms < 1000 ? `${ms}ms` : ms < 60000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60000)}m${Math.round((ms % 60000) / 1000)}s`
}

/** 关键词高亮：返回安全的 HTML */
export function highlight(text, keyword) {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const safe = esc(text)
  const terms = (keyword || '').split(/\s+/).filter(Boolean).map((t) => esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  if (!terms.length) return safe
  return safe.replace(new RegExp(`(${terms.join('|')})`, 'gi'), '<mark>$1</mark>')
}

export function saveBlob(blob, filename) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}
