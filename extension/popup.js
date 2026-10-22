/* 拍卖采集助手：把当前页面加入采集平台 */
const $ = (id) => document.getElementById(id)
const store = {
  get: (keys) => new Promise((r) => chrome.storage.local.get(keys, r)),
  set: (obj) => new Promise((r) => chrome.storage.local.set(obj, r)),
  remove: (keys) => new Promise((r) => chrome.storage.local.remove(keys, r)),
}
let cfg = {}
let tab = null
let template = null

async function api(path, options = {}) {
  const resp = await fetch(cfg.server.replace(/\/$/, '') + '/api' + path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(cfg.token ? { Authorization: `Bearer ${cfg.token}` } : {}), ...(options.headers || {}) },
  })
  const data = resp.status === 204 ? null : await resp.json().catch(() => null)
  if (!resp.ok) {
    if (resp.status === 401) { await store.remove(['token']); cfg.token = null }
    const detail = data?.detail
    throw new Error(Array.isArray(detail) ? detail.map((d) => d.msg).join('；') : detail || `HTTP ${resp.status}`)
  }
  return data
}

function show(view) {
  $('login-view').classList.toggle('hidden', view !== 'login')
  $('main-view').classList.toggle('hidden', view !== 'main')
  $('logout').classList.toggle('hidden', view !== 'main')
}

function result(text, ok, link) {
  const el = $('result')
  el.className = `result ${ok ? 'ok' : 'err'}`
  el.textContent = text
  if (link) {
    const a = document.createElement('a')
    a.href = link; a.target = '_blank'; a.textContent = ' 打开平台查看 →'
    el.appendChild(a)
  }
}

/** 目标页面：当前窗口的活动标签页；若插件页面本身是活动页（在标签页中打开时），取最近访问的其他网页 */
async function targetTab() {
  const [active] = await chrome.tabs.query({ active: true, lastFocusedWindow: true })
  if (active && /^https?:/.test(active.url || '')) return active
  const tabs = (await chrome.tabs.query({})).filter((t) => /^https?:/.test(t.url || ''))
  tabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))
  return tabs[0] || null
}

function hostOf(url) {
  try { return new URL(url).hostname } catch { return '' }
}

function sameSite(a, b) {
  const pa = a.split('.').slice(-2).join('.'); const pb = b.split('.').slice(-2).join('.')
  return pa && pa === pb
}

async function initMain() {
  show('main')
  tab = await targetTab()
  if (!tab) { result('请先打开一个拍卖网页', false); $('add').disabled = true; return }
  $('page-title').textContent = tab.title || tab.url
  $('page-url').textContent = tab.url
  $('name').value = (tab.title || hostOf(tab.url)).slice(0, 60)
  const cookies = await chrome.cookies.getAll({ url: tab.url })
  $('cookie-count').textContent = cookies.length
  try {
    const templates = await api('/templates/builtin')
    template = templates.find((t) => sameSite(hostOf(t.example_url), hostOf(tab.url))) || null
  } catch (e) {
    if (!cfg.token) return initLogin()
    result(e.message, false)
  }
  if (template) {
    const opt = document.createElement('option')
    opt.value = 'template'; opt.textContent = `使用内置模板：${template.name}`
    $('method').prepend(opt)
    $('method').value = 'template'
    $('template-hint').textContent = '已识别该网站，有经过验证的内置模板。'
    $('template-hint').classList.remove('hidden')
  }
  $('method').dispatchEvent(new Event('change'))
}

function initLogin() {
  show('login')
  $('server').value = cfg.server || ''
  $('username').value = cfg.username || ''
}

$('method').addEventListener('change', () => {
  $('hint-wrap').classList.toggle('hidden', $('method').value !== 'ai')
  if ($('method').value === 'template' && template) $('fetch-mode').value = template.fetch_mode
})

$('login').addEventListener('click', async () => {
  cfg.server = $('server').value.trim()
  cfg.username = $('username').value.trim()
  if (!/^https?:\/\//.test(cfg.server)) return result('请填写以 http:// 或 https:// 开头的平台地址', false)
  try {
    const r = await api('/auth/login', { method: 'POST', body: JSON.stringify({ username: cfg.username, password: $('password').value }) })
    cfg.token = r.access_token
    await store.set({ server: cfg.server, username: cfg.username, token: cfg.token })
    $('password').value = ''
    await initMain()
  } catch (e) {
    show('login'); $('result').classList.add('hidden')
    alert(`登录失败：${e.message}`)
  }
})

$('logout').addEventListener('click', async () => {
  await store.remove(['token']); cfg.token = null; initLogin()
})

$('add').addEventListener('click', async () => {
  const btn = $('add')
  btn.disabled = true
  try {
    const method = $('method').value
    const body = { name: $('name').value.trim() || hostOf(tab.url), start_url: tab.url, fetch_mode: $('fetch-mode').value }
    if ($('with-cookies').checked) {
      const cookies = await chrome.cookies.getAll({ url: tab.url })
      body.auth_type = 'cookie'
      body.auth = { cookies: cookies.map((c) => ({ name: c.name, value: c.value, domain: c.domain, path: c.path })) }
    }
    if (method === 'template') { body.rule = template.config; body.rule_origin = 'manual' }
    result('正在添加…', true)
    const source = await api('/sources', { method: 'POST', body: JSON.stringify(body) })
    let msg = `已添加站点「${source.name}」`
    if (method === 'ai') {
      result(`${msg}\nAI 正在分析页面并生成规则，约需 20 秒到 2 分钟…`, true)
      const r = await api('/ai/generate', {
        method: 'POST',
        body: JSON.stringify({ ...body, source_id: source.id, hint: $('hint').value || null, apply: true }),
      })
      msg += r.ok ? `\nAI 已生成并启用规则 v${r.rule_version}，试跑抽取 ${r.count} 条` : '\nAI 未能生成可用规则，请在平台中调整'
    }
    const hasRule = method === 'template' || msg.includes('已生成并启用')
    if (hasRule && $('crawl-now').checked) {
      await api(`/sources/${source.id}/crawl`, { method: 'POST' })
      msg += '\n已开始采集'
    }
    result(msg, true, `${cfg.server.replace(/\/$/, '')}/sources`)
  } catch (e) {
    result(`失败：${e.message}`, false)
  } finally {
    btn.disabled = false
  }
})

;(async () => {
  cfg = await store.get(['server', 'username', 'token'])
  if (cfg.server && cfg.token) await initMain()
  else initLogin()
})()
