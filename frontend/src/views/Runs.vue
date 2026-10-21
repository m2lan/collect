<template>
  <el-card>
    <div class="toolbar">
      <el-select v-model="sourceId" placeholder="全部站点" clearable style="width: 200px" @change="search">
        <el-option v-for="s in sources" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-button @click="load">刷新</el-button>
    </div>
    <el-table :data="rows" v-loading="loading">
      <el-table-column label="#" prop="id" width="70" />
      <el-table-column label="站点" min-width="140"><template #default="{ row }">{{ sourceName(row.source_id) }}</template></el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }"><el-tag :type="RUN_STATUS[row.status]?.type" size="small">{{ RUN_STATUS[row.status]?.label }}</el-tag></template>
      </el-table-column>
      <el-table-column label="规则版本" width="90"><template #default="{ row }">{{ row.rule_version ? `v${row.rule_version}` : '-' }}</template></el-table-column>
      <el-table-column label="页数" prop="pages" width="70" />
      <el-table-column label="抓取 / 新增 / 变化" width="150">
        <template #default="{ row }">{{ row.items_found }} / {{ row.items_new }} / {{ row.items_updated }}</template>
      </el-table-column>
      <el-table-column label="开始时间" width="150"><template #default="{ row }">{{ fmtTime(row.started_at) }}</template></el-table-column>
      <el-table-column label="耗时" width="80"><template #default="{ row }">{{ duration(row) }}</template></el-table-column>
      <el-table-column label="类型" width="90">
        <template #default="{ row }">{{ row.trigger === 'cron' ? '定时' : '手动' }}{{ row.kind === 'live' ? '·盯价' : '' }}</template>
      </el-table-column>
      <el-table-column label="错误信息" min-width="220">
        <template #default="{ row }">
          <el-tooltip v-if="row.error" :content="row.error" placement="top" popper-class="run-error">
            <span class="error">{{ row.error }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="" width="70">
        <template #default="{ row }"><el-button link type="primary" @click="openLog(row)">日志</el-button></template>
      </el-table-column>
    </el-table>
    <el-pagination
      style="margin-top: 12px; justify-content: flex-end" layout="total, prev, pager, next"
      :total="total" v-model:current-page="page" :page-size="20" @current-change="load"
    />
  </el-card>

  <el-drawer v-model="logVisible" :title="`采集日志 #${logRun?.id || ''}`" size="640px">
    <div v-if="logRun" class="log">
      <div v-for="(e, i) in logRun.log" :key="i" :class="['line', e.level]">
        <span class="t">{{ fmtTime(e.t) }}</span>{{ e.msg }}
      </div>
      <div v-if="!logRun.log?.length" class="muted">暂无日志</div>
    </div>
  </el-drawer>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getRun, listRuns, listSources } from '../api'
import { RUN_STATUS, fmtTime } from '../format'

const route = useRoute()
const sourceId = ref(route.query.source_id ? Number(route.query.source_id) : null)
const sources = ref([])
const rows = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const logVisible = ref(false)
const logRun = ref(null)
let timer

async function openLog(row) {
  logRun.value = await getRun(row.id)
  logVisible.value = true
}

const sourceName = (id) => sources.value.find((s) => s.id === id)?.name || `#${id}`
const duration = (r) => (r.started_at && r.finished_at ? `${Math.round((new Date(r.finished_at) - new Date(r.started_at)) / 1000)}s` : '-')

async function load() {
  loading.value = rows.value.length === 0
  try {
    const data = await listRuns({ source_id: sourceId.value || undefined, page: page.value })
    rows.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

onMounted(async () => {
  load()
  sources.value = await listSources()
  // 有执行中的任务时自动刷新
  timer = setInterval(() => {
    if (rows.value.some((r) => r.status === 'pending' || r.status === 'running')) load()
  }, 3000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.log { font-family: Menlo, Consolas, monospace; font-size: 12px; line-height: 1.7; }
.line .t { color: #909399; margin-right: 8px; }
.line.warn { color: #e6a23c; }
.line.error { color: #f56c6c; }
.error { color: #f56c6c; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
<style>
.run-error { max-width: 560px; white-space: pre-wrap; }
</style>
