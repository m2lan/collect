<template>
  <el-card>
    <div class="toolbar">
      <el-select v-model="q.source_id" placeholder="全部站点" clearable style="width: 180px" @change="search">
        <el-option v-for="s in sources" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-select v-model="q.status" placeholder="全部状态" clearable style="width: 130px" @change="search">
        <el-option v-for="(v, k) in STATUS" :key="k" :label="v.label" :value="k" />
      </el-select>
      <el-input v-model="q.keyword" placeholder="标题 / 编号" clearable style="width: 200px" @keyup.enter="search" @clear="search" />
      <el-input-number v-model="q.min_price" :min="0" :controls="false" placeholder="最低价" style="width: 110px" />
      <span style="line-height: 32px">-</span>
      <el-input-number v-model="q.max_price" :min="0" :controls="false" placeholder="最高价" style="width: 110px" />
      <el-select v-model="q.sort" style="width: 150px" @change="search">
        <el-option label="最近采集" value="-last_seen_at" />
        <el-option label="价格从高到低" value="-current_price" />
        <el-option label="价格从低到高" value="current_price" />
        <el-option label="结束时间（近→远）" value="end_time" />
        <el-option label="出价次数最多" value="-bid_count" />
      </el-select>
      <el-button type="primary" @click="search">查询</el-button>
      <el-dropdown style="margin-left: auto" @command="doExport">
        <el-button :loading="exporting">导出<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="xlsx">导出 Excel</el-dropdown-item>
            <el-dropdown-item command="csv">导出 CSV</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <el-table :data="rows" v-loading="loading" @row-click="openDetail" style="cursor: pointer">
      <el-table-column label="图片" width="80">
        <template #default="{ row }">
          <el-image v-if="row.image" :src="row.image" fit="cover" style="width: 50px; height: 50px" lazy />
        </template>
      </el-table-column>
      <el-table-column label="标题" min-width="240">
        <template #default="{ row }">
          <div>{{ row.title || row.unique_key }}</div>
          <div class="muted">{{ row.source_name }}</div>
        </template>
      </el-table-column>
      <el-table-column label="当前价 / 成交价" width="150">
        <template #default="{ row }">
          <b>{{ fmtPrice(row.deal_price ?? row.current_price, row.currency) }}</b>
          <div v-if="row.start_price" class="muted">起拍 {{ fmtPrice(row.start_price, row.currency) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="出价" prop="bid_count" width="70" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag v-if="STATUS[row.status]" disable-transitions :type="STATUS[row.status].type" size="small">{{ STATUS[row.status].label }}</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="结束时间" width="150"><template #default="{ row }">{{ fmtTime(row.end_time) }}</template></el-table-column>
      <el-table-column label="最近采集" width="150"><template #default="{ row }">{{ fmtTime(row.last_seen_at) }}</template></el-table-column>
      <el-table-column label="" width="70">
        <template #default="{ row }">
          <el-link v-if="row.url" :href="row.url" target="_blank" type="primary" @click.stop>原页面</el-link>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      style="margin-top: 12px; justify-content: flex-end"
      layout="total, sizes, prev, pager, next"
      :total="total" v-model:current-page="q.page" v-model:page-size="q.page_size"
      @current-change="load" @size-change="search"
    />
  </el-card>

  <el-drawer v-model="detailVisible" :title="detail?.title || '拍品详情'" size="560px">
    <template v-if="detail">
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="站点">{{ detail.source_name }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ STATUS[detail.status]?.label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="当前价">{{ fmtPrice(detail.current_price, detail.currency) }}</el-descriptions-item>
        <el-descriptions-item label="成交价">{{ fmtPrice(detail.deal_price, detail.currency) }}</el-descriptions-item>
        <el-descriptions-item label="起拍价">{{ fmtPrice(detail.start_price, detail.currency) }}</el-descriptions-item>
        <el-descriptions-item label="出价次数">{{ detail.bid_count ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ fmtTime(detail.start_time) }}</el-descriptions-item>
        <el-descriptions-item label="结束时间">{{ fmtTime(detail.end_time) }}</el-descriptions-item>
        <el-descriptions-item label="首次采集">{{ fmtTime(detail.first_seen_at) }}</el-descriptions-item>
        <el-descriptions-item label="最近采集">{{ fmtTime(detail.last_seen_at) }}</el-descriptions-item>
        <el-descriptions-item v-for="(v, k) in detail.extra" :key="k" :label="k">{{ v }}</el-descriptions-item>
      </el-descriptions>
      <h4>价格走势</h4>
      <PriceChart v-if="detail.snapshots.length > 1" :snapshots="detail.snapshots" :currency="detail.currency" />
      <div v-else class="muted">价格变化两次以上后显示走势图</div>
      <h4>价格变化记录</h4>
      <el-table :data="[...detail.snapshots].reverse()" size="small">
        <el-table-column label="时间"><template #default="{ row }">{{ fmtTime(row.captured_at) }}</template></el-table-column>
        <el-table-column label="价格"><template #default="{ row }">{{ fmtPrice(row.price, detail.currency) }}</template></el-table-column>
        <el-table-column label="出价" prop="bid_count" />
        <el-table-column label="状态"><template #default="{ row }">{{ STATUS[row.status]?.label || '-' }}</template></el-table-column>
      </el-table>
    </template>
  </el-drawer>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'
import { exportLots, getLot, listLots, listSources } from '../api'
import { STATUS, fmtPrice, fmtTime } from '../format'
import PriceChart from '../components/PriceChart.vue'

const q = reactive({ source_id: null, status: null, keyword: '', min_price: null, max_price: null, sort: '-last_seen_at', page: 1, page_size: 20 })
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const sources = ref([])
const detail = ref(null)
const detailVisible = ref(false)
const exporting = ref(false)

async function doExport(format) {
  exporting.value = true
  try {
    const { page, page_size, ...filters } = q
    const params = Object.fromEntries(Object.entries({ ...filters, format }).filter(([, v]) => v !== null && v !== ''))
    const blob = await exportLots(params)
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    const p = (n) => String(n).padStart(2, '0')
    const d = new Date()
    a.download = `拍品数据_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}.${format}`
    a.click()
    URL.revokeObjectURL(a.href)
  } finally {
    exporting.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const params = Object.fromEntries(Object.entries(q).filter(([, v]) => v !== null && v !== ''))
    const data = await listLots(params)
    rows.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function search() {
  q.page = 1
  load()
}

async function openDetail(row) {
  detail.value = await getLot(row.id)
  detailVisible.value = true
}

onMounted(async () => {
  load()
  sources.value = await listSources()
})
</script>
