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
      <el-table-column label="登录" width="90">
        <template #default="{ row }">{{ AUTH_LABEL[row.auth_type] }}</template>
      </el-table-column>
      <el-table-column label="规则" width="70">
        <template #default="{ row }">{{ row.active_rule_version ? `v${row.active_rule_version}` : '未配置' }}</template>
      </el-table-column>
      <el-table-column label="拍品数" prop="lot_count" width="80" />
      <el-table-column label="最近采集" width="170">
        <template #default="{ row }">
          <template v-if="row.last_run">
            <el-tag :type="RUN_STATUS[row.last_run.status]?.type" size="small">{{ RUN_STATUS[row.last_run.status]?.label }}</el-tag>
            <span class="muted"> {{ fmtTime(row.last_run.created_at) }}</span>
          </template>
          <span v-else class="muted">从未采集</span>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="70">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '是' : '否' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="!row.active_rule_version" @click="crawl(row)">立即采集</el-button>
          <el-button link type="primary" @click="openDialog(row)">编辑</el-button>
          <el-button link @click="router.push({ name: 'runs', query: { source_id: row.id } })">记录</el-button>
          <el-popconfirm title="删除站点会同时删除其拍品数据，确定？" @confirm="remove(row)">
            <template #reference><el-button link type="danger">删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

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
      <el-form-item label="补充要求"><el-input v-model="quick.hint" type="textarea" :rows="2" placeholder="可选，如：只抓已成交的拍品，还要抓评估价" /></el-form-item>
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
import { aiGenerate, createSource, crawlSource, deleteSource, getAISettings, getSource, listSources } from '../api'
import { RUN_STATUS, fmtTime } from '../format'
import SourceDialog from '../components/SourceDialog.vue'

const AUTH_LABEL = { none: '无需登录', cookie: 'Cookie', form: '账号密码' }
const router = useRouter()
const rows = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref(null)
const aiAvailable = ref(false)
const quickVisible = ref(false)
const quickLoading = ref(false)
const quick = ref({})
const aiDraft = ref(null)

function openQuick() {
  quick.value = { start_url: '', name: '', fetch_mode: 'browser', hint: '' }
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
      result = await aiGenerate({ source_id: source.id, start_url: q.start_url, fetch_mode: q.fetch_mode, hint: q.hint || null, apply: true })
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

async function crawl(row) {
  const run = await crawlSource(row.id)
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
