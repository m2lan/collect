<template>
  <div class="page">
    <PageHeader title="订阅提醒" desc="符合条件的拍品新上架、价格变化、达到目标价或即将结束时推送到企业微信 / 钉钉 / 飞书">
      <el-button v-if="can('operator')" type="primary" :icon="Plus" @click="open()">新建订阅</el-button>
    </PageHeader>

    <el-tabs v-model="tab">
      <el-tab-pane label="我的订阅" name="subs">
        <el-empty v-if="!subs.length" description="还没有订阅，新建一个试试" />
        <div class="grid sub-grid">
          <el-card v-for="s in subs" :key="s.id" shadow="never" class="sub" :class="{ off: !s.enabled }">
            <div class="sub-head">
              <b>{{ s.name }}</b>
              <el-switch v-if="can('operator')" :model-value="s.enabled" size="small" @change="toggle(s, $event)" />
            </div>
            <div class="conds">
              <span v-for="k in s.keywords" :key="k" class="chip">{{ k }}</span>
              <span v-for="k in s.exclude_keywords" :key="'x' + k" class="chip gray">排除 {{ k }}</span>
              <span v-for="t in s.tags" :key="'t' + t" class="chip">标签 {{ t }}</span>
              <span v-if="s.min_price !== null || s.max_price !== null" class="chip gray">价格 {{ fmtNum(s.min_price ?? 0) }} ~ {{ s.max_price === null ? '不限' : fmtNum(s.max_price) }}</span>
              <span v-if="s.source_ids.length" class="chip gray">{{ s.source_ids.map(sourceName).join('、') }}</span>
              <span v-if="!s.keywords.length && !s.tags.length && !s.source_ids.length" class="chip gray">全部拍品</span>
            </div>
            <div class="events">
              <el-tag v-for="e in s.events" :key="e" disable-transitions size="small" effect="plain">
                {{ EVENTS[e] }}{{ e === 'price_reach' ? ` ≥ ${fmtNum(s.target_price)}` : e === 'ending_soon' ? `（${s.ending_minutes} 分钟）` : '' }}
              </el-tag>
            </div>
            <div class="sub-foot">
              <span class="muted">已推送 {{ s.notified }} 次 · {{ s.webhook_url ? WEBHOOK[s.webhook_type] : '使用全局通知渠道' }}</span>
              <span v-if="can('operator')">
                <el-button link type="primary" size="small" @click="test(s)">测试</el-button>
                <el-button link type="primary" size="small" @click="open(s)">编辑</el-button>
                <el-popconfirm title="确定删除该订阅？" @confirm="remove(s)">
                  <template #reference><el-button link type="danger" size="small">删除</el-button></template>
                </el-popconfirm>
              </span>
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="推送记录" name="inbox">
        <el-card shadow="never">
          <el-table :data="notes" size="small">
            <el-table-column label="时间" width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
            <el-table-column label="订阅" prop="subscription_name" width="140" />
            <el-table-column label="事件" width="110"><template #default="{ row }"><el-tag disable-transitions size="small">{{ EVENTS[row.event] }}</el-tag></template></el-table-column>
            <el-table-column label="内容" min-width="320"><template #default="{ row }"><div class="msg">{{ row.message }}</div></template></el-table-column>
            <el-table-column label="推送" width="90">
              <template #default="{ row }">
                <el-tooltip :disabled="row.sent" :content="row.error || ''"><el-tag disable-transitions size="small" :type="row.sent ? 'success' : 'info'">{{ row.sent ? '已推送' : '仅记录' }}</el-tag></el-tooltip>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination class="pager" layout="total, prev, pager, next" :total="noteTotal" v-model:current-page="notePage" :page-size="20" @current-change="loadNotes" />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="visible" :title="editing ? '编辑订阅' : '新建订阅'" width="640px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="名称" required><el-input v-model="form.name" placeholder="如：清代青花瓷 5 万以下" /></el-form-item>
        <el-form-item label="关键词">
          <el-select v-model="form.keywords" multiple filterable allow-create default-first-option placeholder="标题包含任一即可，回车添加" style="width: 100%" />
        </el-form-item>
        <el-form-item label="排除词">
          <el-select v-model="form.exclude_keywords" multiple filterable allow-create default-first-option placeholder="标题包含任一则排除" style="width: 100%" />
        </el-form-item>
        <el-form-item label="AI 标签">
          <el-select v-model="form.tags" multiple filterable allow-create default-first-option placeholder="品类、年代、材质等，命中任一即可" style="width: 100%">
            <el-option v-for="c in facets.category" :key="c.value" :label="c.value" :value="c.value" />
            <el-option v-for="c in facets.era" :key="'e' + c.value" :label="c.value" :value="c.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="站点">
          <el-select v-model="form.source_ids" multiple placeholder="不选为全部站点" style="width: 100%">
            <el-option v-for="s in sources" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="价格区间">
          <el-input-number v-model="form.min_price" :min="0" :controls="false" placeholder="不限" style="width: 140px" />
          <span style="margin: 0 8px">~</span>
          <el-input-number v-model="form.max_price" :min="0" :controls="false" placeholder="不限" style="width: 140px" />
        </el-form-item>
        <el-form-item label="提醒事件" required>
          <el-checkbox-group v-model="form.events">
            <el-checkbox v-for="(v, k) in EVENTS" :key="k" :value="k">{{ v }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item v-if="form.events.includes('price_reach')" label="目标价">
          <el-input-number v-model="form.target_price" :min="0" :controls="false" style="width: 160px" />
          <span class="muted" style="margin-left: 8px">价格达到或超过时提醒（每件只提醒一次）</span>
        </el-form-item>
        <el-form-item v-if="form.events.includes('ending_soon')" label="提前时间">
          结束前 <el-input-number v-model="form.ending_minutes" :min="1" :max="1440" style="margin: 0 8px; width: 120px" /> 分钟提醒
        </el-form-item>
        <el-form-item label="推送渠道">
          <el-select v-model="form.webhook_type" placeholder="使用全局告警通知渠道" clearable style="width: 200px">
            <el-option v-for="(v, k) in WEBHOOK" :key="k" :label="v" :value="k" />
          </el-select>
          <el-input v-if="form.webhook_type" v-model="form.webhook_url" placeholder="Webhook 地址" style="margin-top: 8px" />
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import {
  createSubscription, deleteSubscription, getFacets, listNotifications, listSources, listSubscriptions,
  testSubscription, updateSubscription,
} from '../api'
import { fmtNum, fmtTime } from '../format'
import { can } from '../store'
import PageHeader from '../components/PageHeader.vue'

const EVENTS = { new: '新上架', price_change: '价格变化', price_reach: '达到目标价', ending_soon: '即将结束' }
const WEBHOOK = { wecom: '企业微信', dingtalk: '钉钉', feishu: '飞书', generic: '通用 Webhook' }
const tab = ref('subs')
const subs = ref([])
const sources = ref([])
const facets = ref({ category: [], era: [] })
const notes = ref([])
const noteTotal = ref(0)
const notePage = ref(1)
const visible = ref(false)
const editing = ref(null)
const form = ref({})

const sourceName = (id) => sources.value.find((s) => s.id === id)?.name || `#${id}`
const blank = () => ({
  name: '', enabled: true, keywords: [], exclude_keywords: [], tags: [], source_ids: [], min_price: null, max_price: null,
  events: ['new'], target_price: null, ending_minutes: 30, webhook_type: null, webhook_url: '',
})

async function load() {
  subs.value = await listSubscriptions()
}

async function loadNotes() {
  const r = await listNotifications({ page: notePage.value, page_size: 20 })
  notes.value = r.items
  noteTotal.value = r.total
}

function open(s = null) {
  editing.value = s
  form.value = s ? { ...blank(), ...s, webhook_url: s.webhook_url || '' } : blank()
  visible.value = true
}

async function save() {
  const body = { ...form.value }
  if (editing.value) await updateSubscription(editing.value.id, body)
  else await createSubscription(body)
  ElMessage.success('已保存')
  visible.value = false
  load()
}

async function toggle(s, enabled) {
  await updateSubscription(s.id, { ...s, enabled })
  load()
}

async function test(s) {
  const r = await testSubscription(s.id)
  if (r.ok) ElMessage.success('测试消息已发送')
  else ElMessageBox.alert(r.message, '发送失败', { type: 'error' })
}

async function remove(s) {
  await deleteSubscription(s.id)
  load()
}

watch(tab, (t) => t === 'inbox' && loadNotes())

onMounted(async () => {
  load()
  ;[sources.value, facets.value] = await Promise.all([listSources(), getFacets()])
})
</script>

<style scoped>
.sub-grid { grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); }
.sub.off { opacity: .6; }
.sub-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 15px; }
.conds { min-height: 26px; }
.events { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
.sub-foot { display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--c-border); padding-top: 8px; }
.msg { white-space: pre-wrap; font-size: 12px; max-height: 90px; overflow: hidden; }
.pager { margin-top: 12px; justify-content: flex-end; }
</style>
