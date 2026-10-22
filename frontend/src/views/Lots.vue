<template>
  <div class="page">
    <PageHeader title="拍品数据" :desc="`共 ${fmtNum(total)} 件拍品；支持多关键词（空格分隔）与同义词搜索`">
      <el-button v-if="can('operator') && facets.untagged" :loading="tagging" @click="tagAll">
        AI 标注未分类拍品（{{ facets.untagged }}）
      </el-button>
      <el-dropdown @command="doExport">
        <el-button type="primary" :loading="exporting">导出<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="xlsx">导出 Excel</el-dropdown-item>
            <el-dropdown-item command="csv">导出 CSV</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </PageHeader>

    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="q.keyword" placeholder="搜索标题、编号、标签…" clearable style="width: 260px" :prefix-icon="Search"
          @keyup.enter="search" @clear="search" />
        <el-select v-model="q.source_id" placeholder="全部站点" clearable style="width: 170px" @change="search">
          <el-option v-for="s in sources" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-select v-model="q.status" placeholder="全部状态" clearable style="width: 120px" @change="search">
          <el-option v-for="(v, k) in STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="q.category" placeholder="全部品类" clearable filterable style="width: 150px" @change="search">
          <el-option v-for="c in facets.category" :key="c.value" :label="`${c.value}（${c.count}）`" :value="c.value" />
        </el-select>
        <el-select v-model="q.era" placeholder="年代" clearable filterable allow-create style="width: 120px" @change="search">
          <el-option v-for="c in facets.era" :key="c.value" :label="`${c.value}（${c.count}）`" :value="c.value" />
        </el-select>
        <div class="range">
          <el-input-number v-model="q.min_price" :min="0" :controls="false" placeholder="最低价" />
          <span>-</span>
          <el-input-number v-model="q.max_price" :min="0" :controls="false" placeholder="最高价" />
        </div>
        <el-select v-model="q.sort" style="width: 150px" @change="search">
          <el-option label="最近采集" value="-last_seen_at" />
          <el-option label="价格从高到低" value="-current_price" />
          <el-option label="价格从低到高" value="current_price" />
          <el-option label="即将结束" value="end_time" />
          <el-option label="出价次数最多" value="-bid_count" />
        </el-select>
        <el-button type="primary" @click="search">查询</el-button>
      </div>

      <el-table :data="rows" v-loading="loading" row-class-name="clickable" @row-click="openDetail">
        <el-table-column width="76">
          <template #default="{ row }">
            <el-image v-if="row.image" :src="row.image" fit="cover" class="thumb" lazy>
              <template #error><div class="thumb ph"><el-icon><Picture /></el-icon></div></template>
            </el-image>
            <div v-else class="thumb ph"><el-icon><Picture /></el-icon></div>
          </template>
        </el-table-column>
        <el-table-column label="拍品" min-width="300">
          <template #default="{ row }">
            <div class="title" v-html="highlight(row.title || row.unique_key, q.keyword)" />
            <div class="meta">
              <span>{{ row.source_name }}</span>
              <span v-if="row.tags?.category" class="chip">{{ row.tags.category }}</span>
              <span v-if="row.tags?.era" class="chip gray">{{ row.tags.era }}</span>
              <span v-if="row.tags?.material" class="chip gray">{{ row.tags.material }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="当前价 / 成交价" width="160" align="right">
          <template #default="{ row }">
            <div class="price">{{ fmtPrice(row.deal_price ?? row.current_price, row.currency) }}</div>
            <div v-if="row.start_price" class="muted">起拍 {{ fmtPrice(row.start_price, row.currency) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="出价" prop="bid_count" width="70" align="center" />
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag v-if="STATUS[row.status]" disable-transitions :type="STATUS[row.status].type" size="small" effect="light">{{ STATUS[row.status].label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结束时间" width="140"><template #default="{ row }"><span class="num">{{ fmtTime(row.end_time) }}</span></template></el-table-column>
        <el-table-column label="最近采集" width="140"><template #default="{ row }"><span class="num muted">{{ fmtTime(row.last_seen_at) }}</span></template></el-table-column>
      </el-table>
      <el-pagination class="pager" layout="total, sizes, prev, pager, next" :total="total"
        v-model:current-page="q.page" v-model:page-size="q.page_size" @current-change="load" @size-change="search" />
    </el-card>

    <el-drawer v-model="detailVisible" size="640px" :title="detail?.title || '拍品详情'">
      <template v-if="detail">
        <div class="detail-head">
          <el-image v-if="detail.image" :src="detail.image" fit="cover" class="cover" :preview-src-list="[detail.image]" />
          <div class="head-info">
            <div class="big price">{{ fmtPrice(detail.deal_price ?? detail.current_price, detail.currency) }}</div>
            <div>
              <el-tag v-if="STATUS[detail.status]" disable-transitions :type="STATUS[detail.status].type" size="small">{{ STATUS[detail.status].label }}</el-tag>
              <span class="muted"> {{ detail.source_name }}</span>
            </div>
            <div class="tags">
              <span v-for="(v, k) in shownTags(detail.tags)" :key="k" class="chip">{{ TAG_LABEL[k] }}：{{ v }}</span>
              <el-button v-if="can('operator') && !detail.tags?.category" link type="primary" size="small" :loading="tagging" @click="tagOne">AI 标注</el-button>
            </div>
            <el-link v-if="detail.url" :href="detail.url" target="_blank" type="primary" class="origin">打开原页面 ↗</el-link>
          </div>
        </div>

        <el-tabs v-model="detailTab">
          <el-tab-pane label="基本信息" name="info">
            <el-descriptions :column="2" border size="small">
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
          </el-tab-pane>
          <el-tab-pane label="价格走势" name="price">
            <PriceChart v-if="detail.snapshots.length > 1" :snapshots="detail.snapshots" :currency="detail.currency" />
            <el-empty v-else description="价格变化两次以上后显示走势图" :image-size="60" />
            <el-table :data="[...detail.snapshots].reverse()" size="small" max-height="260">
              <el-table-column label="时间"><template #default="{ row }">{{ fmtTime(row.captured_at) }}</template></el-table-column>
              <el-table-column label="价格" align="right"><template #default="{ row }">{{ fmtPrice(row.price, detail.currency) }}</template></el-table-column>
              <el-table-column label="出价" prop="bid_count" align="center" />
              <el-table-column label="状态"><template #default="{ row }">{{ STATUS[row.status]?.label || '-' }}</template></el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`同款比价${similar ? `（${similar.items.length}）` : ''}`" name="similar">
            <div v-loading="!similar">
              <template v-if="similar">
                <div v-if="similar.stats.count" class="sim-stats">
                  <div><span class="muted">相似拍品价格中位数</span><b class="num">{{ fmtNum(similar.stats.median) }}</b></div>
                  <div><span class="muted">区间</span><b class="num">{{ fmtNum(similar.stats.min) }} ~ {{ fmtNum(similar.stats.max) }}</b></div>
                  <div v-if="similar.stats.own_vs_median !== null"><span class="muted">本拍品相对中位数</span>
                    <b :class="similar.stats.own_vs_median > 0 ? 'up' : 'down'">{{ similar.stats.own_vs_median > 0 ? '+' : '' }}{{ pct(similar.stats.own_vs_median) }}</b>
                  </div>
                </div>
                <el-empty v-if="!similar.items.length" description="没有找到相似拍品" :image-size="60" />
                <el-table v-else :data="similar.items" size="small">
                  <el-table-column label="拍品" min-width="220">
                    <template #default="{ row }">
                      <el-link :href="row.url" target="_blank" underline="never">{{ row.title }}</el-link>
                      <div class="muted">{{ row.source_name }}<el-tag v-if="!row.same_source" disable-transitions size="small" type="warning" style="margin-left: 4px">其他平台</el-tag></div>
                    </template>
                  </el-table-column>
                  <el-table-column label="价格" width="110" align="right"><template #default="{ row }"><span class="price">{{ fmtPrice(row.price, row.currency) }}</span></template></el-table-column>
                  <el-table-column label="状态" width="80"><template #default="{ row }">{{ STATUS[row.status]?.label || '-' }}</template></el-table-column>
                  <el-table-column label="相似度" width="80" align="right"><template #default="{ row }">{{ pct(row.similarity, 0) }}</template></el-table-column>
                </el-table>
              </template>
            </div>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowDown, Picture, Search } from '@element-plus/icons-vue'
import { aiTag, exportLots, getFacets, getLot, getSimilar, listLots, listSources } from '../api'
import { STATUS, fmtNum, fmtPrice, fmtTime, highlight, pct, saveBlob } from '../format'
import { can } from '../store'
import PageHeader from '../components/PageHeader.vue'
import PriceChart from '../components/PriceChart.vue'

const TAG_LABEL = { category: '品类', sub_category: '细分', era: '年代', material: '材质', origin: '产地', brand: '品牌/作者', condition: '品相' }
const route = useRoute()
const q = reactive({
  source_id: route.query.source_id ? Number(route.query.source_id) : null, status: null, keyword: route.query.keyword || '',
  category: null, era: null, min_price: null, max_price: null, sort: '-last_seen_at', page: 1, page_size: 20,
})
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const sources = ref([])
const facets = ref({ category: [], era: [], untagged: 0 })
const detail = ref(null)
const detailVisible = ref(false)
const detailTab = ref('info')
const similar = ref(null)
const exporting = ref(false)
const tagging = ref(false)

const params = () => Object.fromEntries(Object.entries(q).filter(([, v]) => v !== null && v !== ''))
const shownTags = (tags) => Object.fromEntries(Object.entries(tags || {}).filter(([k, v]) => TAG_LABEL[k] && v))

async function load() {
  loading.value = true
  try {
    const data = await listLots(params())
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
  detailTab.value = 'info'
  similar.value = null
  detail.value = await getLot(row.id)
  detailVisible.value = true
}

watch(detailTab, async (tab) => {
  if (tab === 'similar' && !similar.value && detail.value) similar.value = await getSimilar(detail.value.id)
})

async function doExport(format) {
  exporting.value = true
  try {
    const { page, page_size, ...filters } = params()
    const blob = await exportLots({ ...filters, format })
    const d = new Date()
    const p = (n) => String(n).padStart(2, '0')
    saveBlob(blob, `拍品数据_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}.${format}`)
  } finally {
    exporting.value = false
  }
}

async function tagAll() {
  tagging.value = true
  try {
    const r = await aiTag({ limit: 200 })
    ElMessage.success(`已标注 ${r.tagged} 件${r.failed ? `，${r.failed} 件失败` : ''}`)
    facets.value = await getFacets()
    load()
  } finally {
    tagging.value = false
  }
}

async function tagOne() {
  tagging.value = true
  try {
    await aiTag({ lot_ids: [detail.value.id] })
    detail.value = await getLot(detail.value.id)
    load()
  } finally {
    tagging.value = false
  }
}

onMounted(async () => {
  load()
  ;[sources.value, facets.value] = await Promise.all([listSources(), getFacets()])
})
</script>

<style scoped>
.range { display: flex; align-items: center; gap: 4px; }
.range :deep(.el-input-number) { width: 100px; }
.thumb { width: 52px; height: 52px; border-radius: 8px; display: block; }
.thumb.ph { display: grid; place-items: center; background: #f1f5f9; color: #cbd5e1; font-size: 20px; }
.title { font-weight: 500; line-height: 1.45; }
.meta { margin-top: 4px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; color: var(--c-muted); font-size: 12px; }
.meta .chip { margin: 0; }
.pager { margin-top: 14px; justify-content: flex-end; }
:deep(.clickable) { cursor: pointer; }
.detail-head { display: flex; gap: 16px; margin-bottom: 8px; }
.cover { width: 140px; height: 140px; border-radius: 10px; flex: none; }
.head-info { display: flex; flex-direction: column; gap: 8px; }
.big { font-size: 26px; }
.origin { align-self: flex-start; }
.tags { display: flex; flex-wrap: wrap; align-items: center; }
.sim-stats { display: flex; gap: 24px; padding: 12px 14px; background: #f8fafc; border-radius: 10px; margin-bottom: 10px; }
.sim-stats div { display: flex; flex-direction: column; gap: 2px; }
.sim-stats b { font-size: 17px; }
</style>
