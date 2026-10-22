<template>
  <div class="page">
    <PageHeader title="行情分析" desc="成交率、溢价率、价格分布与走势；也可以直接用自然语言提问">
      <el-button v-if="can('operator')" :icon="Document" @click="openReport">生成周报 / 月报</el-button>
    </PageHeader>

    <el-card v-if="aiAvailable" shadow="never" class="ask">
      <div class="ask-bar">
        <el-icon class="spark"><MagicStick /></el-icon>
        <el-input v-model="question" size="large" placeholder="问问数据：例如“近三个月瓷器的成交均价是多少”“各平台进行中的拍品数量”“最贵的 10 件翡翠”"
          @keyup.enter="ask" />
        <el-button type="primary" size="large" :loading="asking" @click="ask">提问</el-button>
      </div>
      <div class="examples">
        <span v-for="e in EXAMPLES" :key="e" class="chip gray" @click="question = e; ask()">{{ e }}</span>
      </div>
      <div v-if="answer" class="answer">
        <div class="answer-text"><b>{{ answer.plan.title || answer.question }}</b><p>{{ answer.answer }}</p></div>
        <div v-if="answer.plan.chart === 'number' && answer.rows.length === 1" class="bignum num">{{ fmtValue(answer.rows[0].value, answer.plan.metric) }}</div>
        <Chart v-else-if="['bar', 'line', 'pie'].includes(answer.plan.chart) && answer.plan.mode !== 'list'" :option="askOption" height="300px" />
        <el-table v-else :data="answer.rows" size="small" max-height="360">
          <template v-if="answer.plan.mode === 'list'">
            <el-table-column label="拍品" min-width="260"><template #default="{ row }"><el-link :href="row.url" target="_blank" underline="never">{{ row.title }}</el-link></template></el-table-column>
            <el-table-column label="站点" prop="source" width="130" />
            <el-table-column label="价格" width="120" align="right"><template #default="{ row }"><span class="price">{{ fmtPrice(row.price, row.currency) }}</span></template></el-table-column>
            <el-table-column label="状态" prop="status" width="90" />
            <el-table-column label="结束时间" width="140"><template #default="{ row }">{{ fmtTime(row.end_time) }}</template></el-table-column>
          </template>
          <template v-else>
            <el-table-column :label="answer.group_label || '项目'" prop="label" />
            <el-table-column :label="answer.metric_label" align="right"><template #default="{ row }">{{ fmtValue(row.value, answer.plan.metric) }}</template></el-table-column>
            <el-table-column v-if="answer.rows[0]?.count !== undefined" label="拍品数" prop="count" align="right" />
          </template>
        </el-table>
        <div class="muted">基于 {{ answer.total }} 件拍品计算 · 查询计划由 AI 生成、平台执行，不会修改数据</div>
      </div>
    </el-card>

    <el-card shadow="never">
      <div class="toolbar" style="margin: 0">
        <el-select v-model="f.days" style="width: 120px" @change="load">
          <el-option v-for="d in [7, 30, 90, 180, 365]" :key="d" :label="`近 ${d} 天`" :value="d" />
        </el-select>
        <el-select v-model="f.source_id" placeholder="全部站点" clearable style="width: 170px" @change="load">
          <el-option v-for="s in sources" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-select v-model="f.category" placeholder="全部品类" clearable filterable style="width: 150px" @change="load">
          <el-option v-for="c in facets.category" :key="c.value" :label="c.value" :value="c.value" />
        </el-select>
        <el-input v-model="f.keyword" placeholder="关键词，如 翡翠 手镯" clearable style="width: 200px" @keyup.enter="load" @clear="load" />
        <el-radio-group v-model="f.period" @change="load">
          <el-radio-button value="week">按周</el-radio-button>
          <el-radio-button value="month">按月</el-radio-button>
        </el-radio-group>
        <el-button type="primary" @click="load">分析</el-button>
      </div>
    </el-card>

    <template v-if="data">
      <div class="grid grid-stats">
        <StatCard label="拍品数" :value="fmtNum(s.lots)" :sub="`进行中 ${s.live} · 即将开始 ${s.upcoming}`" />
        <StatCard label="成交率" :value="s.sold_rate === null ? '-' : (s.sold_rate * 100).toFixed(1)" unit="%" :sub="`已结束 ${s.closed}，成交 ${s.sold}`" tone="success" />
        <StatCard label="溢价率（中位）" :value="s.premium_median === null ? '-' : (s.premium_median * 100).toFixed(1)" unit="%" :sub="`平均 ${pct(s.premium_avg)}，成交价相对起拍价`" tone="warning" />
        <StatCard label="成交价中位数" :value="fmtNum(s.deal_price.median)" :sub="`四分位 ${fmtNum(s.deal_price.p25)} ~ ${fmtNum(s.deal_price.p75)}`" />
        <StatCard label="最高成交价" :value="fmtNum(s.deal_price.max)" :sub="`平均 ${fmtNum(s.deal_price.avg)}`" tone="danger" />
      </div>

      <el-card shadow="never">
        <template #header><div class="card-title">走势 <small>拍品数量、成交量与成交中位价</small></div></template>
        <Chart v-if="data.trend.length" :option="trendOption" height="320px" />
        <el-empty v-else description="暂无数据" :image-size="60" />
      </el-card>

      <div class="grid grid-2">
        <el-card shadow="never">
          <template #header><div class="card-title">分品类 <small>需 AI 标注后统计</small></div></template>
          <el-table :data="data.by_category" size="small" max-height="380">
            <el-table-column label="品类" prop="name" />
            <el-table-column label="拍品" prop="lots" align="right" width="70" />
            <el-table-column label="进行中" prop="live" align="right" width="70" />
            <el-table-column label="成交率" align="right" width="80"><template #default="{ row }">{{ pct(row.sold_rate) }}</template></el-table-column>
            <el-table-column label="成交中位价" align="right" width="110"><template #default="{ row }">{{ fmtNum(row.median_price) }}</template></el-table-column>
          </el-table>
        </el-card>
        <el-card shadow="never">
          <template #header><div class="card-title">分站点</div></template>
          <el-table :data="data.by_source" size="small" max-height="380">
            <el-table-column label="站点" prop="name" />
            <el-table-column label="拍品" prop="lots" align="right" width="70" />
            <el-table-column label="进行中" prop="live" align="right" width="70" />
            <el-table-column label="成交率" align="right" width="80"><template #default="{ row }">{{ pct(row.sold_rate) }}</template></el-table-column>
            <el-table-column label="成交中位价" align="right" width="110"><template #default="{ row }">{{ fmtNum(row.median_price) }}</template></el-table-column>
          </el-table>
        </el-card>
      </div>
    </template>

    <el-dialog v-model="reportVisible" title="行情报告" width="720px">
      <div class="toolbar">
        <el-radio-group v-model="reportForm.period">
          <el-radio-button value="week">周报（近 7 天）</el-radio-button>
          <el-radio-button value="month">月报（近 30 天）</el-radio-button>
        </el-radio-group>
        <span class="muted">按当前筛选的站点、品类、关键词生成</span>
        <el-button type="primary" :loading="reporting" @click="genReport(false)">生成</el-button>
      </div>
      <div v-loading="reporting" class="report" v-html="reportHtml" />
      <template #footer>
        <el-button @click="copyReport">复制文本</el-button>
        <el-button type="primary" :disabled="!report" :loading="reporting" @click="genReport(true)">推送到通知渠道</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Document, MagicStick } from '@element-plus/icons-vue'
import { askData, getAISettings, getAnalytics, getFacets, listSources, makeReport } from '../api'
import { fmtNum, fmtPrice, fmtTime, pct } from '../format'
import { can } from '../store'
import Chart from '../components/Chart.vue'
import PageHeader from '../components/PageHeader.vue'
import StatCard from '../components/StatCard.vue'

const EXAMPLES = ['近 30 天各品类的成交率', '各站点进行中的拍品数量', '最贵的 10 件拍品', '最近每周的成交中位价走势']
const f = reactive({ days: 90, source_id: null, category: null, keyword: '', period: 'week' })
const data = ref(null)
const sources = ref([])
const facets = ref({ category: [] })
const aiAvailable = ref(false)
const question = ref('')
const asking = ref(false)
const answer = ref(null)
const reportVisible = ref(false)
const reporting = ref(false)
const report = ref(null)
const reportForm = reactive({ period: 'week' })
const s = computed(() => data.value?.summary || {})

const fmtValue = (v, metric) => (v === null || v === undefined ? '-' : ['sold_rate', 'premium'].includes(metric) ? pct(v) : fmtNum(v, 2))

const trendOption = computed(() => {
  const t = data.value?.trend || []
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, right: 0 },
    grid: { left: 50, right: 60, top: 36, bottom: 24 },
    xAxis: { type: 'category', data: t.map((x) => x.period) },
    yAxis: [{ type: 'value', name: '件数', minInterval: 1 }, { type: 'value', name: '中位价', splitLine: { show: false } }],
    series: [
      { name: '拍品数', type: 'bar', data: t.map((x) => x.lots), itemStyle: { color: '#bfd0f9', borderRadius: [4, 4, 0, 0] } },
      { name: '成交', type: 'bar', data: t.map((x) => x.sold), itemStyle: { color: '#2563eb', borderRadius: [4, 4, 0, 0] } },
      { name: '成交中位价', type: 'line', yAxisIndex: 1, smooth: true, data: t.map((x) => x.median_price), itemStyle: { color: '#f59e0b' } },
    ],
  }
})

const askOption = computed(() => {
  const a = answer.value
  if (!a) return null
  const rows = a.rows
  if (a.plan.chart === 'pie') {
    return {
      tooltip: { trigger: 'item' },
      legend: { orient: 'vertical', right: 10, top: 'middle' },
      series: [{ type: 'pie', radius: ['40%', '70%'], center: ['40%', '50%'], data: rows.map((r) => ({ name: r.label, value: r.value })) }],
    }
  }
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: rows.map((r) => r.label), axisLabel: { interval: 0, rotate: rows.length > 8 ? 30 : 0 } },
    yAxis: { type: 'value' },
    series: [{ name: a.metric_label, type: a.plan.chart, smooth: true, data: rows.map((r) => r.value), itemStyle: { borderRadius: [4, 4, 0, 0] } }],
  }
})

const params = () => Object.fromEntries(Object.entries(f).filter(([, v]) => v !== null && v !== ''))

async function load() {
  data.value = await getAnalytics(params())
}

async function ask() {
  if (!question.value.trim()) return
  asking.value = true
  try {
    answer.value = await askData(question.value.trim())
  } finally {
    asking.value = false
  }
}

function openReport() {
  report.value = null
  reportVisible.value = true
  genReport(false)
}

async function genReport(push) {
  reporting.value = true
  try {
    report.value = await makeReport({ period: reportForm.period, source_id: f.source_id, category: f.category, keyword: f.keyword || null, push })
    if (push) ElMessage.success('已推送到告警通知渠道')
  } finally {
    reporting.value = false
  }
}

// 简单渲染报告中的 Markdown（标题与列表）
const reportHtml = computed(() => {
  const md = report.value?.markdown || ''
  const esc = (t) => t.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
  return md.split('\n').map((line) => {
    if (line.startsWith('# ')) return `<h2>${esc(line.slice(2))}</h2>`
    if (line.startsWith('## ')) return `<h3>${esc(line.slice(3))}</h3>`
    if (line.startsWith('- ')) return `<li>${esc(line.slice(2))}</li>`
    return line ? `<p>${esc(line)}</p>` : ''
  }).join('')
})

async function copyReport() {
  await navigator.clipboard?.writeText(report.value?.markdown || '')
  ElMessage.success('已复制')
}

onMounted(async () => {
  load()
  ;[sources.value, facets.value] = await Promise.all([listSources(), getFacets()])
  aiAvailable.value = (await getAISettings()).available
})
</script>

<style scoped>
.ask { background: linear-gradient(135deg, #eff4ff, #f5f3ff); border-color: #dbe4ff; }
.ask-bar { display: flex; gap: 10px; align-items: center; }
.spark { font-size: 24px; color: #6366f1; }
.examples { margin: 10px 0 0 34px; }
.examples .chip { cursor: pointer; background: #fff; }
.answer { margin-top: 14px; padding: 16px; background: #fff; border-radius: 10px; border: 1px solid var(--c-border); display: flex; flex-direction: column; gap: 10px; }
.answer-text p { margin: 4px 0 0; color: var(--c-text-2); }
.bignum { font-size: 40px; font-weight: 700; color: var(--c-primary); }
.report { max-height: 55vh; overflow-y: auto; padding: 4px 8px; }
.report :deep(h2) { font-size: 18px; margin: 4px 0 10px; }
.report :deep(h3) { font-size: 15px; margin: 14px 0 6px; }
.report :deep(li) { margin: 4px 0 4px 18px; }
</style>
