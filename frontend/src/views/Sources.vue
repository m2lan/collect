<template>
  <el-card>
    <div class="page-header">
      <span class="muted">添加拍卖平台的列表页链接并配置采集规则</span>
      <div>
        <el-button v-if="aiAvailable" type="warning" :icon="MagicStick" @click="openQuick">AI 添加站点</el-button>
        <el-button type="primary" :icon="Plus" @click="openDialog()">新增站点</el-button>
      </div>
    </div>
    <el-table :data="rows" v-loading="loading">
      <el-table-column label="名称" prop="name" min-width="140" />
      <el-table-column label="链接" min-width="240">
        <template #default="{ row }"><el-link :href="row.start_url" target="_blank">{{ row.start_url }}</el-link></template>
      </el-table-column>
      <el-table-column label="抓取方式" width="100">
        <template #default="{ row }">{{ row.fetch_mode === 'browser' ? '浏览器' : '静态' }}</template>
      </el-table-column>
      <el-table-column label="登录" width="110">
        <template #default="{ row }">
          <span>{{ AUTH_LABEL[row.auth_type] }}</span>
          <el-tooltip v-if="row.login_expired_at" :content="`登录已失效（${fmtTime(row.login_expired_at)}），点击重新登录`">
            <el-tag disable-transitions type="danger" size="small" style="margin-left: 4px; cursor: pointer" @click="remote(row)">失效</el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="规则" width="70">
        <template #default="{ row }">{{ row.active_rule_version ? `v${row.active_rule_version}` : '未配置' }}</template>
      </el-table-column>
      <el-table-column label="拍品数" prop="lot_count" width="80" />
      <el-table-column label="健康" width="80">
        <template #default="{ row }">
          <el-tooltip :disabled="!row.health_message" :content="row.health_message || ''" placement="top">
            <el-tag disable-transitions :type="HEALTH[row.health].type" size="small">{{ HEALTH[row.health].label }}</el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="最近采集" width="170">
        <template #default="{ row }">
          <template v-if="row.last_run">
            <el-tag disable-transitions :type="RUN_STATUS[row.last_run.status]?.type" size="small">{{ RUN_STATUS[row.last_run.status]?.label }}</el-tag>
            <span class="muted"> {{ fmtTime(row.last_run.created_at) }}</span>
          </template>
          <span v-else class="muted">从未采集</span>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="70">
        <template #default="{ row }"><el-tag disable-transitions :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '是' : '否' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="270" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="!row.active_rule_version" @click="crawl(row)">立即采集</el-button>
          <el-button link type="primary" :disabled="!row.active_rule_version" @click="crawl(row, 'live')">盯价</el-button>
          <el-button link type="primary" @click="openDialog(row)">编辑</el-button>
          <el-button link @click="router.push({ name: 'runs', query: { source_id: row.id } })">记录</el-button>
          <el-popconfirm title="删除站点会同时删除其拍品数据，确定？" @confirm="remove(row)">
            <template #reference><el-button link type="danger">删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <RemoteBrowser v-model="remoteVisible" :source="remoteSource" @saved="load" />
  <SourceDialog v-model="dialogVisible" :source="editing" :ai-draft="aiDraft" @saved="load" />

  <el-dialog v-model="quickVisible" title="AI 添加站点" width="560px" :close-on-click-modal="false">
    <el-form :model="quick" label-width="100px">
      <el-form-item label="列表页链接" required><el-input v-model="quick.start_url" placeholder="https://..." /></el-form-item>
      <el-form-item label="名称"><el-input v-model="quick.name" placeholder="留空则使用域名" /></el-form-item>
      <el-form-item label="抓取方式">
        <el-radio-group v-model="quick.fetch_mode">
          <el-radio value="browser">浏览器渲染</el-radio>
          <el-radio value="static">静态请求</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="补充要求"><el-input v-model="quick.hint" type="textarea" :rows="2" placeholder="可选，如：只抓已成交的拍品，还要抓保证金和评估价" /></el-form-item>
      <el-form-item label="详情页">
        <el-checkbox v-model="quick.analyze_detail">同时分析详情页</el-checkbox>
        <div class="muted">保证金、评估价等只在详情页的字段需要勾选（AI 发现列表页缺少你要求的字段时也会自动分析）</div>
      </el-form-item>
      <div class="muted">需要登录的站点请使用"新增站点"配置登录方式后，再在"采集规则"中使用 AI 生成。</div>
      <el-alert v-if="quickLoading" type="info" :closable="false" style="margin-top: 12px" title="AI 正在分析页面并生成规则，通常需要 20 秒到 2 分钟…" />
    </el-form>
    <template #footer>
      <el-button @click="quickVisible = false">取消</el-button>
      <el-button type="warning" :loading="quickLoading" @click="submitQuick">开始生成</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { MagicStick, Plus } from '@element-plus/icons-vue'
import { aiGenerate, createSource, crawlLive, crawlSource, deleteSource, getAISettings, getSource, listSources } from '../api'
import { RUN_STATUS, fmtTime } from '../format'
import SourceDialog from '../components/SourceDialog.vue'
import RemoteBrowser from '../components/RemoteBrowser.vue'

const AUTH_LABEL = { none: '无需登录', cookie: 'Cookie', form: '账号密码', session: '人工登录' }
const HEALTH = { ok: { label: '正常', type: 'success' }, warning: { label: '异常', type: 'warning' }, broken: { label: '失效', type: 'danger' } }
const router = useRouter()
const rows = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref(null)
const aiAvailable = ref(false)
const remoteVisible = ref(false)
const remoteSource = ref(null)

function remote(row) {
  remoteSource.value = row
  remoteVisible.value = true
}
const quickVisible = ref(false)
const quickLoading = ref(false)
const quick = ref({})
const aiDraft = ref(null)

function openQuick() {
  quick.value = { start_url: '', name: '', fetch_mode: 'browser', hint: '', analyze_detail: false }
  quickVisible.value = true
}

async function submitQuick() {
  const q = quick.value
  if (!q.start_url) return ElMessage.warning('请填写列表页链接')
  quickLoading.value = true
  try {
    let name = q.name
    if (!name) {
      try { name = new URL(q.start_url).hostname } catch { name = q.start_url }
    }
    const source = await createSource({ name, start_url: q.start_url, fetch_mode: q.fetch_mode })
    let result = null
    try {
      result = await aiGenerate({
        source_id: source.id, start_url: q.start_url, fetch_mode: q.fetch_mode, hint: q.hint || null,
        analyze_detail: q.analyze_detail, apply: true,
      })
    } catch {
      // 错误已提示；站点已创建，可在编辑中继续调整
    }
    quickVisible.value = false
    await load()
    if (result?.ok) {
      ElMessage.success(`AI 已生成并启用规则 v${result.rule_version}，试跑抽取到 ${result.count} 条`)
    } else {
      ElMessage.warning('AI 未能生成可用规则，请在编辑页检查并调整')
      openDialog(await getSource(source.id), result)
    }
  } finally {
    quickLoading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    rows.value = await listSources()
  } finally {
    loading.value = false
  }
}

function openDialog(row = null, draft = null) {
  editing.value = row
  aiDraft.value = draft
  dialogVisible.value = true
}

async function crawl(row, kind = 'full') {
  const run = kind === 'live' ? await crawlLive(row.id) : await crawlSource(row.id)
  ElMessage.success(`已创建采集任务 #${run.id}`)
  load()
}

async function remove(row) {
  await deleteSource(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(async () => {
  load()
  aiAvailable.value = (await getAISettings()).available
})
</script>
