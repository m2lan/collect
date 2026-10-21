<template>
  <el-card>
    <template #header><b>AI 功能</b></template>
    <el-form :model="ai" label-width="150px" style="max-width: 640px">
      <el-form-item label="启用 AI">
        <el-switch v-model="ai.enabled" />
        <span class="muted" style="margin-left: 12px">关闭后隐藏所有 AI 入口，后端拒绝 AI 请求</span>
      </el-form-item>
      <el-form-item label="每日调用上限">
        <el-input-number v-model="ai.daily_limit" :min="0" /> <span class="muted">&nbsp;0 表示不限</span>
      </el-form-item>
      <el-form-item label="最大修正轮数">
        <el-input-number v-model="ai.max_rounds" :min="1" :max="6" />
        <span class="muted">&nbsp;生成后试跑发现问题时，自动让 AI 修正的最大轮数</span>
      </el-form-item>
      <el-form-item label="规则失效自动修复">
        <el-switch v-model="ai.auto_repair" />
        <span class="muted" style="margin-left: 12px">采集规则失效时自动调用 AI 重新生成并直接启用</span>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="saveSettings">保存</el-button>
        <el-tag disable-transitions v-if="ai.enabled" :type="ai.available ? 'success' : 'warning'" style="margin-left: 12px">
          {{ ai.available ? 'AI 可用' : '未配置可用模型或已达今日上限' }}
        </el-tag>
      </el-form-item>
    </el-form>
  </el-card>

  <el-card style="margin-top: 16px">
    <template #header><b>采集设置</b></template>
    <el-form :model="crawler" label-width="150px" style="max-width: 760px">
      <el-form-item label="代理池">
        <el-input v-model="proxiesText" type="textarea" :rows="4" class="mono"
          placeholder="每行一个，如：&#10;http://user:pass@1.2.3.4:8080&#10;socks5://5.6.7.8:1080" />
        <div class="muted">站点开启"使用代理"后，每次采集随机选择一个</div>
      </el-form-item>
      <el-form-item label="随机 User-Agent">
        <el-switch v-model="crawler.random_ua" />
        <span class="muted" style="margin-left: 12px">每次采集随机使用常见浏览器的 UA（站点自定义请求头优先）</span>
      </el-form-item>
      <el-form-item><el-button type="primary" @click="saveCrawler">保存</el-button></el-form-item>
    </el-form>
  </el-card>

  <el-card style="margin-top: 16px">
    <template #header><b>规则模板</b></template>
    <el-table :data="templates" size="small">
      <el-table-column label="名称" prop="name" min-width="140" />
      <el-table-column label="说明" prop="description" min-width="200" show-overflow-tooltip />
      <el-table-column label="抓取方式" width="100"><template #default="{ row }">{{ row.config.fetch?.mode === 'api' ? '接口' : row.fetch_mode === 'browser' ? '浏览器' : '静态' }}</template></el-table-column>
      <el-table-column label="字段数" width="80"><template #default="{ row }">{{ row.config.list.fields.length }}</template></el-table-column>
      <el-table-column label="创建时间" width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
      <el-table-column label="" width="80">
        <template #default="{ row }">
          <el-popconfirm title="确定删除该模板？" @confirm="removeTemplate(row)">
            <template #reference><el-button link type="danger">删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>
    <div class="muted" style="margin-top: 8px">在站点编辑的"采集规则"中可将规则保存为模板，或从模板导入。</div>
  </el-card>

  <el-card style="margin-top: 16px">
    <template #header><b>告警通知</b></template>
    <el-form :model="alert" label-width="150px" style="max-width: 760px">
      <el-form-item label="失效判定">
        连续失败 <el-input-number v-model="alert.failure_threshold" :min="1" :max="20" size="small" style="margin: 0 8px" /> 次判定规则失效
      </el-form-item>
      <el-form-item label="通知方式">
        <el-select v-model="alert.webhook_type" style="width: 200px">
          <el-option label="不通知" value="none" />
          <el-option label="企业微信群机器人" value="wecom" />
          <el-option label="钉钉群机器人" value="dingtalk" />
          <el-option label="飞书群机器人" value="feishu" />
          <el-option label="通用 Webhook（JSON）" value="generic" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="alert.webhook_type !== 'none'" label="Webhook 地址">
        <el-input v-model="alert.webhook_url" placeholder="https://..." />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="saveAlert">保存</el-button>
        <el-button v-if="alert.webhook_type !== 'none'" :loading="alertTesting" @click="sendTestAlert">发送测试消息</el-button>
      </el-form-item>
    </el-form>
  </el-card>

  <el-card style="margin-top: 16px">
    <template #header>
      <div class="page-header" style="margin: 0">
        <b>模型配置</b>
        <el-button type="primary" size="small" @click="openProvider()">添加模型</el-button>
      </div>
    </template>
    <div class="muted" style="margin-bottom: 8px">支持 OpenAI 协议（OpenAI、DeepSeek、Gemini、通义千问、Kimi 等兼容服务）与 Anthropic 协议（Claude 等）。</div>
    <el-table :data="providers">
      <el-table-column label="名称" prop="name" min-width="120" />
      <el-table-column label="协议" width="100"><template #default="{ row }">{{ row.protocol === 'openai' ? 'OpenAI' : 'Anthropic' }}</template></el-table-column>
      <el-table-column label="模型" prop="model" min-width="140" />
      <el-table-column label="Base URL" min-width="200"><template #default="{ row }">{{ row.base_url || '（官方默认）' }}</template></el-table-column>
      <el-table-column label="API Key" prop="api_key_masked" width="130" />
      <el-table-column label="能力" width="140">
        <template #default="{ row }">
          <el-tag disable-transitions v-if="row.supports_tools" size="small">工具调用</el-tag>
          <el-tag disable-transitions v-if="row.supports_vision" size="small" type="success" style="margin-left: 4px">视觉</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag disable-transitions v-if="row.is_default" type="warning" size="small">默认</el-tag>
          <el-tag disable-transitions :type="row.enabled ? 'success' : 'info'" size="small" style="margin-left: 4px">{{ row.enabled ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button link type="primary" :loading="testing === row.id" @click="test(row)">测试连接</el-button>
          <el-button link type="primary" @click="openProvider(row)">编辑</el-button>
          <el-popconfirm title="确定删除？" @confirm="remove(row)">
            <template #reference><el-button link type="danger">删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-card style="margin-top: 16px">
    <template #header>
      <b>AI 调用记录</b>
      <span class="muted" style="margin-left: 12px">累计 {{ usage.calls }} 次，输入 {{ usage.prompt_tokens }} tokens，输出 {{ usage.completion_tokens }} tokens</span>
    </template>
    <el-table :data="generations" size="small">
      <el-table-column label="#" prop="id" width="60" />
      <el-table-column label="时间" width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
      <el-table-column label="触发" width="80"><template #default="{ row }">{{ row.trigger === 'repair' ? '自动修复' : '手动' }}</template></el-table-column>
      <el-table-column label="URL" prop="url" min-width="200" show-overflow-tooltip />
      <el-table-column label="结果" width="80">
        <template #default="{ row }"><el-tag disable-transitions :type="row.status === 'success' ? 'success' : row.status === 'failed' ? 'danger' : 'info'" size="small">{{ { success: '成功', failed: '失败', running: '进行中' }[row.status] }}</el-tag></template>
      </el-table-column>
      <el-table-column label="轮数" prop="rounds" width="60" />
      <el-table-column label="条数" prop="item_count" width="60" />
      <el-table-column label="Tokens" width="120"><template #default="{ row }">{{ row.prompt_tokens }} / {{ row.completion_tokens }}</template></el-table-column>
      <el-table-column label="耗时" width="70"><template #default="{ row }">{{ (row.duration_ms / 1000).toFixed(1) }}s</template></el-table-column>
      <el-table-column label="说明 / 错误" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ row.error || row.explanation }}</template>
      </el-table-column>
    </el-table>
    <el-pagination style="margin-top: 12px; justify-content: flex-end" layout="total, prev, pager, next"
      :total="genTotal" v-model:current-page="genPage" :page-size="10" @current-change="loadGenerations" />
  </el-card>

  <el-dialog v-model="providerVisible" :title="editing ? '编辑模型' : '添加模型'" width="620px">
    <el-form :model="pf" label-width="120px">
      <el-form-item label="名称" required><el-input v-model="pf.name" placeholder="如：Claude / DeepSeek" /></el-form-item>
      <el-form-item label="协议" required>
        <el-radio-group v-model="pf.protocol">
          <el-radio value="anthropic">Anthropic</el-radio>
          <el-radio value="openai">OpenAI</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="Base URL">
        <el-autocomplete v-model="pf.base_url" :fetch-suggestions="baseUrlSuggestions" placeholder="留空使用官方地址" style="width: 100%" />
      </el-form-item>
      <el-form-item label="API Key" :required="!editing">
        <el-input v-model="pf.api_key" type="password" show-password :placeholder="editing ? `已保存 ${editing.api_key_masked}，留空不修改` : ''" />
      </el-form-item>
      <el-form-item label="模型" required><el-input v-model="pf.model" placeholder="如 claude-sonnet-5-5 / deepseek-chat / gemini-2.5-pro" /></el-form-item>
      <el-form-item label="最大输出 tokens"><el-input-number v-model="pf.max_tokens" :min="256" :step="1024" /></el-form-item>
      <el-form-item label="Temperature">
        <el-input-number v-model="pf.temperature" :min="0" :max="2" :step="0.1" :precision="1" :value-on-clear="null" />
        <span class="muted">&nbsp;留空则不传（部分新模型不支持该参数）</span>
      </el-form-item>
      <el-form-item label="超时（秒）"><el-input-number v-model="pf.timeout" :min="10" :max="600" /></el-form-item>
      <el-form-item label="能力">
        <el-checkbox v-model="pf.supports_tools">支持工具调用</el-checkbox>
        <el-checkbox v-model="pf.supports_vision">支持图片输入</el-checkbox>
      </el-form-item>
      <el-form-item label="状态">
        <el-checkbox v-model="pf.enabled">启用</el-checkbox>
        <el-checkbox v-model="pf.is_default">设为默认</el-checkbox>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="providerVisible = false">取消</el-button>
      <el-button type="primary" @click="saveProvider">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  aiUsage, createProvider, deleteProvider, deleteTemplate, getAISettings, getAlertSettings, getCrawlerSettings,
  listGenerations, listProviders, listTemplates, putAISettings, putAlertSettings, putCrawlerSettings, testAlert,
  testProvider, updateProvider,
} from '../api'
import { fmtTime } from '../format'

const BASE_URLS = {
  anthropic: ['https://api.anthropic.com'],
  openai: [
    'https://api.openai.com/v1',
    'https://api.deepseek.com/v1',
    'https://generativelanguage.googleapis.com/v1beta/openai/',
    'https://dashscope.aliyuncs.com/compatible-mode/v1',
    'https://api.moonshot.cn/v1',
  ],
}

const ai = ref({ enabled: false, daily_limit: 100, auto_repair: false, max_rounds: 3, available: false })
const providers = ref([])
const generations = ref([])
const genTotal = ref(0)
const genPage = ref(1)
const usage = ref({ calls: 0, prompt_tokens: 0, completion_tokens: 0 })
const providerVisible = ref(false)
const editing = ref(null)
const pf = ref({})
const testing = ref(null)
const alert = ref({ failure_threshold: 2, webhook_type: 'none', webhook_url: '' })
const alertTesting = ref(false)
const crawler = ref({ proxies: [], random_ua: true })
const proxiesText = ref('')
const templates = ref([])

async function saveCrawler() {
  crawler.value = await putCrawlerSettings({ ...crawler.value, proxies: proxiesText.value.split('\n') })
  proxiesText.value = crawler.value.proxies.join('\n')
  ElMessage.success('已保存')
}

async function removeTemplate(row) {
  await deleteTemplate(row.id)
  templates.value = await listTemplates()
}

async function saveAlert() {
  alert.value = await putAlertSettings(alert.value)
  ElMessage.success('已保存')
}

async function sendTestAlert() {
  alertTesting.value = true
  try {
    const r = await testAlert(alert.value)
    if (r.ok) ElMessage.success('测试消息已发送')
    else ElMessageBox.alert(r.message, '发送失败', { type: 'error' })
  } finally {
    alertTesting.value = false
  }
}

const baseUrlSuggestions = (q, cb) => cb(BASE_URLS[pf.value.protocol].filter((u) => !q || u.includes(q)).map((value) => ({ value })))

async function load() {
  ai.value = await getAISettings()
  alert.value = await getAlertSettings()
  crawler.value = await getCrawlerSettings()
  proxiesText.value = crawler.value.proxies.join('\n')
  templates.value = await listTemplates()
  providers.value = await listProviders()
  usage.value = await aiUsage()
  loadGenerations()
}

async function loadGenerations() {
  const data = await listGenerations({ page: genPage.value, page_size: 10 })
  generations.value = data.items
  genTotal.value = data.total
}

async function saveSettings() {
  const { available, ...body } = ai.value
  ai.value = await putAISettings(body)
  ElMessage.success('已保存')
}

function openProvider(row = null) {
  editing.value = row
  pf.value = row
    ? { ...row, api_key: '' }
    : { name: '', protocol: 'anthropic', base_url: '', api_key: '', model: '', max_tokens: 8192, temperature: null, timeout: 120, supports_tools: true, supports_vision: false, is_default: false, enabled: true }
  providerVisible.value = true
}

async function saveProvider() {
  const body = { ...pf.value }
  if (!body.api_key) delete body.api_key
  if (editing.value) await updateProvider(editing.value.id, body)
  else await createProvider(body)
  providerVisible.value = false
  ElMessage.success('已保存')
  load()
}

async function test(row) {
  testing.value = row.id
  try {
    const r = await testProvider(row.id)
    if (r.ok) ElMessage.success(r.message)
    else ElMessageBox.alert(r.message, '连接失败', { type: 'error' })
  } finally {
    testing.value = null
  }
}

async function remove(row) {
  await deleteProvider(row.id)
  load()
}

onMounted(load)
</script>
