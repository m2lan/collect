<template>
  <div class="page" v-loading="!data">
    <PageHeader title="采集看板" desc="各站点采集运行状况、失败原因与数据增长">
      <el-radio-group v-model="days" size="small" @change="load">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="14">近 14 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
      </el-radio-group>
      <el-button :icon="Refresh" size="small" @click="load">刷新</el-button>
    </PageHeader>

    <template v-if="data">
      <div class="grid grid-stats">
        <StatCard label="采集站点" :value="t.sources" :sub="`启用 ${t.sources_enabled} 个`" />
        <StatCard label="拍品总数" :value="fmtNum(t.lots)" :sub="`24 小时新增 ${fmtNum(t.new_lots_24h)}`" />
        <StatCard label="进行中拍品" :value="fmtNum(t.live_lots)" tone="warning" />
        <StatCard label="24 小时价格变化" :value="fmtNum(t.price_changes_24h)" />
        <StatCard label="24 小时采集成功率" :value="t.success_rate_24h === null ? '-' : (t.success_rate_24h * 100).toFixed(1)" unit="%"
          :sub="`${t.success_24h} 成功 / ${t.failed_24h} 失败`" :tone="t.success_rate_24h === null || t.success_rate_24h >= 0.9 ? 'success' : 'danger'" />
        <StatCard label="平均采集耗时" :value="fmtDuration(t.avg_duration_ms_24h)" sub="近 24 小时" />
      </div>

      <div class="grid grid-2">
        <el-card shadow="never">
          <template #header><div class="card-title">采集趋势 <small>每日成功 / 失败次数与新增拍品</small></div></template>
          <Chart :option="trendOption" />
        </el-card>
        <el-card shadow="never">
          <template #header><div class="card-title">失败原因 <small>近 {{ days }} 天</small></div></template>
          <Chart v-if="data.failure_reasons.length" :option="reasonOption" />
          <el-empty v-else description="没有失败，一切正常" :image-size="80" />
        </el-card>
      </div>

      <el-card shadow="never">
        <template #header><div class="card-title">站点运行情况 <small>近 {{ days }} 天</small></div></template>
        <el-table :data="data.sources" size="default">
          <el-table-column label="站点" min-width="180">
            <template #default="{ row }">
              <router-link :to="{ name: 'runs', query: { source_id: row.id } }" class="link">{{ row.name }}</router-link>
              <el-tag v-if="!row.enabled" disable-transitions size="small" type="info" style="margin-left: 6px">已停用</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="健康" width="90">
            <template #default="{ row }"><el-tag disable-transitions size="small" :type="HEALTH[row.health]?.type">{{ HEALTH[row.health]?.label }}</el-tag></template>
          </el-table-column>
          <el-table-column label="登录" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.login_expired" disable-transitions size="small" type="danger">已失效</el-tag>
              <span v-else class="muted">正常</span>
            </template>
          </el-table-column>
          <el-table-column label="成功率" min-width="180">
            <template #default="{ row }">
              <el-progress v-if="row.success_rate !== null" :percentage="Math.round(row.success_rate * 100)" :stroke-width="8"
                :status="row.success_rate >= 0.9 ? 'success' : row.success_rate >= 0.6 ? 'warning' : 'exception'" />
              <span v-else class="muted">无记录</span>
            </template>
          </el-table-column>
          <el-table-column label="采集次数" width="110"><template #default="{ row }">{{ row.success }} / {{ row.runs }}</template></el-table-column>
          <el-table-column label="平均耗时" width="100"><template #default="{ row }">{{ fmtDuration(row.avg_duration_ms) }}</template></el-table-column>
          <el-table-column label="拍品数" width="100" align="right"><template #default="{ row }"><span class="num">{{ fmtNum(row.lots) }}</span></template></el-table-column>
          <el-table-column label="最近采集" width="150"><template #default="{ row }">{{ fmtTime(row.last_run_at) }}</template></el-table-column>
        </el-table>
      </el-card>

      <el-card shadow="never">
        <template #header><div class="card-title">最近失败 <router-link to="/runs" class="link small">全部采集记录 →</router-link></div></template>
        <el-empty v-if="!data.recent_failures.length" description="暂无失败记录" :image-size="60" />
        <div v-for="f in data.recent_failures" :key="f.id" class="fail">
          <el-tag disable-transitions size="small" type="danger">{{ f.label }}</el-tag>
          <b>{{ f.source_name }}</b>
          <span class="err">{{ f.error }}</span>
          <span class="muted">{{ fmtTime(f.created_at) }}</span>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getDashboard } from '../api'
import { HEALTH, fmtDuration, fmtNum, fmtTime } from '../format'
import Chart from '../components/Chart.vue'
import PageHeader from '../components/PageHeader.vue'
import StatCard from '../components/StatCard.vue'

const days = ref(14)
const data = ref(null)
const t = computed(() => data.value?.totals || {})

const trendOption = computed(() => {
  const d = data.value?.daily || []
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, right: 0 },
    grid: { left: 40, right: 40, top: 36, bottom: 24 },
    xAxis: { type: 'category', data: d.map((x) => x.date.slice(5)) },
    yAxis: [{ type: 'value', name: '次数', minInterval: 1 }, { type: 'value', name: '新增', splitLine: { show: false } }],
    series: [
      { name: '成功', type: 'bar', stack: 'runs', data: d.map((x) => x.success), itemStyle: { color: '#22c55e', borderRadius: [0, 0, 0, 0] } },
      { name: '失败', type: 'bar', stack: 'runs', data: d.map((x) => x.failed), itemStyle: { color: '#ef4444', borderRadius: [4, 4, 0, 0] } },
      { name: '新增拍品', type: 'line', yAxisIndex: 1, smooth: true, data: d.map((x) => x.new_lots), itemStyle: { color: '#2563eb' } },
    ],
  }
})

const reasonOption = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}：{c} 次（{d}%）' },
  legend: { orient: 'vertical', right: 10, top: 'middle' },
  series: [{
    type: 'pie', radius: ['45%', '72%'], center: ['38%', '50%'], itemStyle: { borderColor: '#fff', borderWidth: 2 },
    label: { show: false }, data: (data.value?.failure_reasons || []).map((r) => ({ name: r.label, value: r.count })),
  }],
}))

async function load() {
  data.value = await getDashboard({ days: days.value })
}

onMounted(load)
</script>

<style scoped>
.link { color: var(--c-primary); text-decoration: none; font-weight: 500; }
.link.small { font-size: 13px; font-weight: 400; }
.fail { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px dashed var(--c-border); font-size: 13px; }
.fail:last-child { border-bottom: 0; }
.fail .err { flex: 1; color: var(--c-text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
