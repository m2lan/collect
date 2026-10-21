<template>
  <el-dialog :model-value="modelValue" :title="source ? `编辑站点 - ${source.name}` : '新增站点'" width="960px"
    top="5vh" :close-on-click-modal="false" @update:model-value="emit('update:modelValue', $event)" @open="init">
    <el-tabs v-model="tab">
      <el-tab-pane label="基本信息" name="basic">
        <el-form :model="form" label-width="110px">
          <el-form-item label="名称" required><el-input v-model="form.name" placeholder="如：某某拍卖-瓷器专场" /></el-form-item>
          <el-form-item label="列表页链接" required><el-input v-model="form.start_url" placeholder="https://..." /></el-form-item>
          <el-form-item label="抓取方式">
            <el-radio-group v-model="form.fetch_mode">
              <el-radio value="static">静态请求（快）</el-radio>
              <el-radio value="browser">浏览器渲染（支持 JS 页面）</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="请求频率">
            <el-input-number v-model="form.rate_limit" :min="0.1" :max="20" :step="0.5" /> <span class="muted">&nbsp;次/秒（翻页间隔）</span>
          </el-form-item>
          <el-form-item label="请求头">
            <el-input v-model="headersText" type="textarea" :rows="3" class="mono" placeholder='{"Referer": "https://..."}' />
          </el-form-item>
          <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="登录设置" name="auth">
        <el-form :model="form.auth" label-width="130px">
          <el-form-item label="登录方式">
            <el-radio-group v-model="form.auth_type">
              <el-radio value="none">无需登录</el-radio>
              <el-radio value="cookie">粘贴 Cookie</el-radio>
              <el-radio value="form">账号密码自动登录</el-radio>
            </el-radio-group>
          </el-form-item>
          <template v-if="form.auth_type === 'cookie'">
            <el-form-item label="Cookie">
              <el-input v-model="form.auth.cookies" type="textarea" :rows="5" class="mono"
                :placeholder="cookiePlaceholder" />
              <div class="muted">支持 <code>a=b; c=d</code> 格式，或浏览器插件导出的 JSON 数组。适用于有验证码/短信登录的平台。</div>
            </el-form-item>
          </template>
          <template v-if="form.auth_type === 'form'">
            <el-form-item label="登录页 URL" required><el-input v-model="form.auth.login_url" /></el-form-item>
            <el-form-item label="账号" required><el-input v-model="form.auth.username" /></el-form-item>
            <el-form-item label="密码" required>
              <el-input v-model="form.auth.password" type="password" show-password
                :placeholder="source?.auth?.has_password ? '已保存，留空则不修改' : ''" />
            </el-form-item>
            <el-form-item label="账号输入框选择器" required><el-input v-model="form.auth.username_selector" placeholder="#username" /></el-form-item>
            <el-form-item label="密码输入框选择器" required><el-input v-model="form.auth.password_selector" placeholder="#password" /></el-form-item>
            <el-form-item label="登录按钮选择器" required><el-input v-model="form.auth.submit_selector" placeholder="button[type=submit]" /></el-form-item>
            <el-form-item label="已登录标志选择器">
              <el-input v-model="form.auth.logged_in_selector" placeholder=".user-name（页面上只有登录后才出现的元素）" />
              <div class="muted">用于判断登录成功与登录是否失效，失效时自动重新登录。</div>
            </el-form-item>
            <el-form-item v-if="source?.auth?.has_session" label="登录会话">
              <el-tag type="success" size="small">已保存</el-tag>
              <el-button link type="danger" style="margin-left: 8px" @click="logout">清除会话</el-button>
            </el-form-item>
          </template>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="采集规则" name="rule">
        <div class="toolbar">
          <el-button @click="ruleText = JSON.stringify(EXAMPLE_RULE, null, 2)">填入示例</el-button>
          <el-button @click="formatRule">格式化</el-button>
          <el-button type="success" :loading="previewing" @click="runPreview">试跑（第 1 页）</el-button>
          <span class="muted" style="line-height: 32px">按当前未保存的配置抓取并展示结果，不写入数据</span>
        </div>
        <el-input v-model="ruleText" type="textarea" :rows="16" class="mono" spellcheck="false" />
        <el-collapse style="margin-top: 8px">
          <el-collapse-item title="规则说明" name="help">
            <div class="muted help">
              <p><b>list.item_selector</b>：每个拍品容器的 CSS 选择器（或设置 item_selector_type 为 xpath）。</p>
              <p><b>fields[]</b>：key 为字段名；标准字段 title / url / image / current_price / start_price / deal_price / bid_count / status / start_time / end_time 会进入对应列，其余字段进入"扩展信息"。</p>
              <p>type：css / xpath / regex；selector 相对拍品容器，留空表示容器本身；attr：text / html / 属性名（href、src、data-*）。</p>
              <p>transform：trim、price（解析金额与币种，支持"万"）、number、int、datetime、absolute_url、status（进行中/已成交/流拍…）、lower，或 {"name":"regex_replace","pattern":"..","repl":".."}。</p>
              <p><b>unique_key</b>：用于去重的字段，默认 url。<b>pagination</b>：none / next_link（selector）/ url_template（template 含 {page}）。</p>
              <p><b>fetch</b>：浏览器模式下可设置 wait_for（等待元素）、scroll（滚动加载）、wait_ms。</p>
            </div>
          </el-collapse-item>
        </el-collapse>
        <template v-if="previewResult">
          <el-divider />
          <div class="muted" style="margin-bottom: 8px">
            抓取 {{ previewResult.pages }} 页，抽取到 <b>{{ previewResult.count }}</b> 条
          </div>
          <el-alert v-for="e in previewResult.errors" :key="e" :title="e" type="warning" :closable="false" style="margin-bottom: 4px" />
          <el-table :data="previewResult.items" size="small" max-height="320" border>
            <el-table-column v-for="col in previewColumns" :key="col" :label="col" :prop="col" min-width="140" show-overflow-tooltip />
          </el-table>
        </template>
      </el-tab-pane>

      <el-tab-pane v-if="source" label="规则版本" name="versions">
        <el-table :data="rules" size="small">
          <el-table-column label="版本" width="80"><template #default="{ row }">v{{ row.version }}</template></el-table-column>
          <el-table-column label="来源" prop="origin" width="90" />
          <el-table-column label="备注" prop="note" />
          <el-table-column label="创建时间" width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
          <el-table-column label="状态" width="180">
            <template #default="{ row }">
              <el-tag v-if="row.is_active" type="success" size="small">使用中</el-tag>
              <template v-else>
                <el-button link type="primary" @click="activate(row)">切换到此版本</el-button>
                <el-button link @click="ruleText = JSON.stringify(row.config, null, 2); tab = 'rule'">查看</el-button>
              </template>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { activateRule, clearSession, createSource, listRules, preview, updateSource } from '../api'
import { fmtTime } from '../format'

const props = defineProps({ modelValue: Boolean, source: Object })
const emit = defineEmits(['update:modelValue', 'saved'])

const EXAMPLE_RULE = {
  fetch: { wait_for: null, scroll: false, wait_ms: 0, timeout: 30 },
  list: {
    item_selector: '.lot-item',
    unique_key: 'url',
    fields: [
      { key: 'title', selector: '.title', transform: ['trim'] },
      { key: 'url', selector: 'a', attr: 'href', transform: ['absolute_url'] },
      { key: 'image', selector: 'img', attr: 'src', transform: ['absolute_url'] },
      { key: 'current_price', selector: '.price', transform: ['price'] },
      { key: 'bid_count', selector: '.bids', transform: ['int'] },
      { key: 'status', selector: '.status', transform: ['status'] },
      { key: 'end_time', selector: '.end-time', transform: ['datetime'] },
    ],
  },
  pagination: { type: 'next_link', selector: 'a.next', max_pages: 5 },
}
const cookiePlaceholder = 'sessionid=xxx; token=yyy\n或 [{"name":"sessionid","value":"xxx","domain":".example.com"}]'
const emptyAuth = () => ({
  cookies: '', login_url: '', username: '', password: '',
  username_selector: '', password_selector: '', submit_selector: '', logged_in_selector: '',
})

const tab = ref('basic')
const form = ref({})
const headersText = ref('')
const ruleText = ref('')
const rules = ref([])
const saving = ref(false)
const previewing = ref(false)
const previewResult = ref(null)

const previewColumns = computed(() => {
  const cols = new Set()
  previewResult.value?.items.forEach((it) => Object.keys(it).forEach((k) => cols.add(k)))
  return [...cols]
})

async function init() {
  const s = props.source
  tab.value = 'basic'
  previewResult.value = null
  form.value = {
    name: s?.name || '',
    start_url: s?.start_url || '',
    fetch_mode: s?.fetch_mode || 'static',
    rate_limit: s?.rate_limit ?? 1,
    enabled: s?.enabled ?? true,
    auth_type: s?.auth_type || 'none',
    auth: { ...emptyAuth(), ...pickAuth(s?.auth) },
  }
  headersText.value = s && Object.keys(s.headers || {}).length ? JSON.stringify(s.headers, null, 2) : ''
  ruleText.value = s?.active_rule ? JSON.stringify(s.active_rule, null, 2) : ''
  rules.value = s ? await listRules(s.id) : []
}

function pickAuth(a) {
  if (!a) return {}
  const { login_url, username, username_selector, password_selector, submit_selector, logged_in_selector } = a
  return Object.fromEntries(
    Object.entries({ login_url, username, username_selector, password_selector, submit_selector, logged_in_selector })
      .map(([k, v]) => [k, v || '']),
  )
}

function parseJson(text, label) {
  if (!text.trim()) return null
  try {
    return JSON.parse(text)
  } catch (e) {
    ElMessage.error(`${label} 不是合法的 JSON：${e.message}`)
    throw e
  }
}

function formatRule() {
  const rule = parseJson(ruleText.value, '采集规则')
  if (rule) ruleText.value = JSON.stringify(rule, null, 2)
}

function payload() {
  const auth = Object.fromEntries(Object.entries(form.value.auth).filter(([, v]) => v))
  return {
    ...form.value,
    auth,
    headers: parseJson(headersText.value, '请求头') || {},
    rule: parseJson(ruleText.value, '采集规则'),
  }
}

async function runPreview() {
  const body = payload()
  if (!body.rule) return ElMessage.warning('请先填写采集规则')
  previewing.value = true
  try {
    previewResult.value = await preview({ ...body, source_id: props.source?.id })
    if (!previewResult.value.count) ElMessage.warning('没有抽取到数据，请检查 item_selector 或登录设置')
  } finally {
    previewing.value = false
  }
}

async function save() {
  const body = payload()
  saving.value = true
  try {
    if (props.source) await updateSource(props.source.id, body)
    else await createSource(body)
    ElMessage.success('已保存')
    emit('update:modelValue', false)
    emit('saved')
  } finally {
    saving.value = false
  }
}

async function activate(row) {
  await activateRule(props.source.id, row.id)
  ElMessage.success(`已切换到 v${row.version}`)
  rules.value = await listRules(props.source.id)
  ruleText.value = JSON.stringify(row.config, null, 2)
  emit('saved')
}

async function logout() {
  await clearSession(props.source.id)
  ElMessage.success('已清除，下次采集时将重新登录')
  emit('saved')
}
</script>

<style scoped>
.help p { margin: 4px 0; }
</style>
