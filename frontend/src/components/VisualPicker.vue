<template>
  <el-dialog :model-value="modelValue" title="可视化选取" fullscreen :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)" @open="load">
    <div class="picker">
      <div class="frame-wrap" v-loading="loading" element-loading-text="正在抓取页面…">
        <div class="toolbar">
          <el-radio-group v-model="mode" size="small">
            <el-radio-button value="item">① 选择拍品</el-radio-button>
            <el-radio-button value="field">② 添加字段</el-radio-button>
            <el-radio-button value="next">③ 选择"下一页"</el-radio-button>
          </el-radio-group>
          <template v-if="mode === 'field'">
            <el-select v-model="fieldKey" filterable allow-create default-first-option size="small" style="width: 200px" placeholder="字段名">
              <el-option v-for="f in FIELD_PRESETS" :key="f.key" :label="`${f.label}（${f.key}）`" :value="f.key" />
            </el-select>
          </template>
          <span class="muted">{{ HINT[mode] }}</span>
        </div>
        <iframe ref="frame" class="frame" sandbox="allow-same-origin" :srcdoc="html" @load="attach" />
      </div>

      <div class="panel">
        <h4>拍品容器</h4>
        <el-input v-model="itemSelector" size="small" placeholder="点击页面中的一个拍品" @change="highlightItems">
          <template #append>{{ itemCount }} 个</template>
        </el-input>
        <div v-if="chain.length" class="chain">
          <el-button size="small" :disabled="chainIndex >= chain.length - 1" @click="moveChain(1)">扩大到上一级</el-button>
          <el-button size="small" :disabled="chainIndex <= 0" @click="moveChain(-1)">缩小到下一级</el-button>
        </div>

        <h4>字段（{{ fields.length }}）</h4>
        <div v-if="!fields.length" class="muted">选择拍品后，切换到"添加字段"，选择字段名再点击页面中对应的内容。</div>
        <div v-for="(f, i) in fields" :key="f.key" class="field">
          <div class="field-head">
            <b>{{ f.key }}</b>
            <el-button link type="danger" size="small" @click="fields.splice(i, 1); refreshSamples()">删除</el-button>
          </div>
          <el-input v-model="f.selector" size="small" placeholder="（容器本身）" @change="refreshSamples">
            <template #prepend>
              <el-select v-model="f.attr" size="small" style="width: 92px" filterable allow-create @change="refreshSamples">
                <el-option v-for="a in attrOptions(f)" :key="a" :label="a" :value="a" />
              </el-select>
            </template>
          </el-input>
          <div class="samples">
            <span v-for="(v, j) in f.samples" :key="j" class="sample" :title="v">{{ v ?? '（空）' }}</span>
          </div>
          <div class="muted">transform: {{ f.transform.join(', ') || '无' }}</div>
        </div>

        <h4>翻页</h4>
        <el-input v-model="nextSelector" size="small" placeholder='切换到"选择下一页"后点击页面上的下一页链接' clearable>
          <template #prepend>
            <el-select v-model="nextType" size="small" style="width: 100px">
              <el-option label="链接" value="next_link" />
              <el-option label="点击按钮" value="click" />
            </el-select>
          </template>
        </el-input>
        <div v-if="nextType === 'click'" class="muted">该"下一页"没有真实链接，将通过点击按钮翻页（需要浏览器渲染模式）</div>
        <div class="row">
          <span class="muted">最多</span>
          <el-input-number v-model="maxPages" :min="1" :max="500" size="small" />
          <span class="muted">页</span>
        </div>
      </div>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :disabled="!itemSelector || !fields.length" @click="finish">生成规则</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { snapshot } from '../api'

const props = defineProps({ modelValue: Boolean, request: Object, baseRule: Object })
const emit = defineEmits(['update:modelValue', 'done'])

const FIELD_PRESETS = [
  { key: 'title', label: '标题', transform: ['trim'] },
  { key: 'url', label: '链接', transform: ['absolute_url'] },
  { key: 'image', label: '图片', transform: ['absolute_url'] },
  { key: 'current_price', label: '当前价', transform: ['price'] },
  { key: 'start_price', label: '起拍价', transform: ['price'] },
  { key: 'deal_price', label: '成交价', transform: ['price'] },
  { key: 'bid_count', label: '出价次数', transform: ['int'] },
  { key: 'status', label: '状态', transform: ['status'] },
  { key: 'start_time', label: '开始时间', transform: ['datetime'] },
  { key: 'end_time', label: '结束时间', transform: ['datetime'] },
  { key: 'lot_no', label: '拍品编号', transform: ['trim'] },
  { key: 'estimate', label: '估价', transform: ['trim'] },
]
const HINT = {
  item: '点击页面中任意一个拍品，自动识别所有同类拍品',
  field: '选择字段名后，点击某个拍品中对应的内容',
  next: '点击页面上的"下一页"链接',
}
const BAD_CLASS = /^(active|hover|selected|current|cur|on|first|last|odd|even|clearfix|clear|fl|fr|left|right|show|hide|hidden|open)$|^(css|sc|jsx|emotion|styled|svelte)-|\d{3,}|__pk-/i

const frame = ref(null)
const html = ref('')
const loading = ref(false)
const mode = ref('item')
const fieldKey = ref('title')
const itemSelector = ref('')
const itemCount = ref(0)
const chain = ref([])
const chainIndex = ref(0)
const fields = ref([])
const nextSelector = ref('')
const nextType = ref('next_link')
const maxPages = ref(5)
let doc = null
let hovered = null

const attrOptions = (f) => [...new Set(['text', 'href', 'src', 'data-src', 'data-original', 'title', 'html', f.attr])]

async function load() {
  mode.value = 'item'
  fields.value = []
  itemSelector.value = ''
  itemCount.value = 0
  chain.value = []
  nextSelector.value = props.baseRule?.pagination?.selector || ''
  nextType.value = props.baseRule?.pagination?.type === 'click' ? 'click' : 'next_link'
  maxPages.value = props.baseRule?.pagination?.max_pages || 5
  html.value = ''
  loading.value = true
  try {
    const r = await snapshot(props.request)
    html.value = r.html
  } catch {
    emit('update:modelValue', false)
  } finally {
    loading.value = false
  }
}

function attach() {
  doc = frame.value?.contentDocument
  if (!doc || !html.value) return
  const style = doc.createElement('style')
  style.textContent = `
    .__pk-hover { outline: 2px dashed #409eff !important; cursor: crosshair !important; }
    .__pk-item { outline: 2px solid #e6a23c !important; outline-offset: -2px; }
    .__pk-field { outline: 2px solid #67c23a !important; background: rgba(103,194,58,.12) !important; }
    .__pk-next { outline: 2px solid #f56c6c !important; }`
  doc.head.appendChild(style)
  doc.addEventListener('mouseover', (e) => {
    hovered?.classList.remove('__pk-hover')
    hovered = e.target
    hovered.classList?.add('__pk-hover')
  })
  doc.addEventListener('click', onClick, true)
  doc.addEventListener('submit', (e) => e.preventDefault(), true)
  // 页面已有规则时预先高亮
  if (props.baseRule?.list?.item_selector) {
    itemSelector.value = props.baseRule.list.item_selector
    if (props.baseRule.list.item_selector_type !== 'xpath') {
      fields.value = (props.baseRule.list.fields || [])
        .filter((f) => (f.type || 'css') === 'css')
        .map((f) => ({ key: f.key, selector: f.selector || '', attr: f.attr || 'text', transform: (f.transform || []).map((t) => (typeof t === 'string' ? t : t.name)), samples: [] }))
      highlightItems()
    }
  }
}

function onClick(e) {
  e.preventDefault()
  e.stopPropagation()
  const el = e.target
  if (mode.value === 'item') pickItem(el)
  else if (mode.value === 'field') pickField(el)
  else pickNext(el)
}

const esc = (s) => (window.CSS?.escape ? CSS.escape(s) : s.replace(/([^\w-])/g, '\\$1'))
const stableClasses = (el) => [...el.classList].filter((c) => !BAD_CLASS.test(c)).slice(0, 2)

function simpleSel(el) {
  return el.tagName.toLowerCase() + stableClasses(el).map((c) => `.${esc(c)}`).join('')
}

function containerSel(el) {
  if (stableClasses(el).length) return simpleSel(el)
  const parent = el.parentElement
  return parent && parent !== doc.body ? `${simpleSel(parent)} > ${el.tagName.toLowerCase()}` : el.tagName.toLowerCase()
}

function count(sel) {
  try {
    return doc.querySelectorAll(sel).length
  } catch {
    return 0
  }
}

function pickItem(target) {
  const candidates = []
  for (let el = target; el && el !== doc.body && el !== doc.documentElement; el = el.parentElement) {
    const sel = containerSel(el)
    const n = count(sel)
    const siblings = el.parentElement ? [...el.parentElement.children].filter((c) => c.matches(sel)).length : 0
    candidates.push({ sel, n, siblings })
  }
  if (!candidates.length) return
  // 优先：在同一父元素下重复出现的最内层元素（典型的列表项）
  let idx = candidates.findIndex((c) => c.n >= 2 && c.siblings >= 2)
  if (idx === -1) idx = candidates.findIndex((c) => c.n >= 2)
  if (idx === -1) idx = 0
  chain.value = candidates
  chainIndex.value = idx
  itemSelector.value = candidates[idx].sel
  highlightItems()
  if (candidates[idx].n >= 2) {
    mode.value = 'field'
    ElMessage.success(`识别到 ${candidates[idx].n} 个拍品，现在可以添加字段`)
  }
}

function moveChain(delta) {
  chainIndex.value += delta
  itemSelector.value = chain.value[chainIndex.value].sel
  highlightItems()
}

function items() {
  try {
    return itemSelector.value ? [...doc.querySelectorAll(itemSelector.value)] : []
  } catch {
    return []
  }
}

function clearMarks(cls) {
  doc?.querySelectorAll(`.${cls}`).forEach((el) => el.classList.remove(cls))
}

function highlightItems() {
  if (!doc) return
  clearMarks('__pk-item')
  const list = items()
  list.forEach((el) => el.classList.add('__pk-item'))
  itemCount.value = list.length
  refreshSamples()
}

function relativeSel(container, el) {
  if (el === container) return ''
  const path = []
  for (let n = el; n && n !== container; n = n.parentElement) path.unshift(n)
  for (let i = path.length - 1; i >= 0; i--) {
    const sel = path.slice(i).map(simpleSel).join(' ')
    try {
      if (container.querySelector(sel) === el) return sel
    } catch { /* 选择器无效时继续尝试 */ }
  }
  return path
    .map((n) => {
      const same = [...n.parentElement.children].filter((c) => c.tagName === n.tagName)
      return `${n.tagName.toLowerCase()}:nth-of-type(${same.indexOf(n) + 1})`
    })
    .join(' > ')
}

function pickField(target) {
  const container = items().find((c) => c.contains(target))
  if (!container) return ElMessage.warning('请点击已高亮（橙色框）的拍品内部的内容')
  const key = fieldKey.value
  if (!key) return ElMessage.warning('请先选择字段名')
  let el = target
  let attr = 'text'
  if (key === 'url') {
    el = target.closest('a') || target.querySelector('a') || target
    if (!container.contains(el) && el !== container) el = target
    attr = 'href'
  } else if (key === 'image') {
    el = target.tagName === 'IMG' ? target : target.querySelector('img') || target
    attr = ['data-src', 'data-original'].find((a) => el.getAttribute(a)) || 'src'
  }
  const preset = FIELD_PRESETS.find((f) => f.key === key)
  const field = { key, selector: relativeSel(container, el), attr, transform: preset ? [...preset.transform] : ['trim'], samples: [] }
  const i = fields.value.findIndex((f) => f.key === key)
  if (i >= 0) fields.value.splice(i, 1, field)
  else fields.value.push(field)
  // 自动切换到下一个尚未添加的常用字段
  const next = FIELD_PRESETS.find((f) => !fields.value.some((x) => x.key === f.key))
  if (next) fieldKey.value = next.key
  refreshSamples()
}

function valueOf(node, attr) {
  if (!node) return null
  if (attr === 'text') return node.textContent.replace(/\s+/g, ' ').trim() || null
  if (attr === 'html') return node.innerHTML.slice(0, 80)
  return node.getAttribute(attr)
}

function refreshSamples() {
  if (!doc) return
  clearMarks('__pk-field')
  const list = items()
  for (const f of fields.value) {
    f.samples = list.slice(0, 3).map((c) => {
      let node = c
      try {
        node = f.selector ? c.querySelector(f.selector) : c
      } catch {
        node = null
      }
      return valueOf(node, f.attr)
    })
    list.forEach((c) => {
      try {
        const node = f.selector ? c.querySelector(f.selector) : null
        node?.classList.add('__pk-field')
      } catch { /* 忽略无效选择器 */ }
    })
  }
}

function pickNext(target) {
  const a = target.closest('a') || target.closest('button, [role=button]') || target
  const href = (a.getAttribute('href') || '').trim().toLowerCase()
  // 没有真实链接（按钮、javascript:、#）时需要点击翻页
  nextType.value = a.tagName === 'A' && href && !href.startsWith('javascript') && href !== '#' ? 'next_link' : 'click'
  clearMarks('__pk-next')
  let sel = simpleSel(a)
  if (count(sel) !== 1) {
    const path = []
    for (let n = a; n && n !== doc.body; n = n.parentElement) {
      path.unshift(n)
      sel = path.map(simpleSel).join(' > ')
      if (count(sel) === 1) break
    }
    if (count(sel) !== 1) {
      sel = path
        .map((n) => {
          const same = [...n.parentElement.children].filter((c) => c.tagName === n.tagName)
          return `${simpleSel(n)}:nth-of-type(${same.indexOf(n) + 1})`
        })
        .join(' > ')
    }
  }
  nextSelector.value = sel
  a.classList.add('__pk-next')
}

const hasUrl = computed(() => fields.value.some((f) => f.key === 'url'))

function finish() {
  const base = props.baseRule || {}
  const rule = {
    fetch: base.fetch || {},
    list: {
      item_selector: itemSelector.value,
      item_selector_type: 'css',
      unique_key: hasUrl.value ? 'url' : null,
      fields: fields.value.map(({ key, selector, attr, transform }) => ({ key, type: 'css', selector, attr, transform })),
    },
    pagination: nextSelector.value
      ? { type: nextType.value, selector: nextSelector.value, max_pages: maxPages.value }
      : base.pagination?.type === 'url_template' ? base.pagination : { type: 'none' },
  }
  if (nextSelector.value && nextType.value === 'click') rule.fetch = { ...rule.fetch, mode: 'browser' }
  emit('done', rule)
  emit('update:modelValue', false)
}
</script>

<style scoped>
.picker { display: flex; gap: 12px; height: calc(100vh - 140px); }
.frame-wrap { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.frame { flex: 1; width: 100%; border: 1px solid #dcdfe6; border-radius: 4px; background: #fff; }
.panel { width: 360px; overflow-y: auto; padding-right: 4px; }
.panel h4 { margin: 12px 0 6px; }
.chain { margin-top: 6px; }
.field { border: 1px solid #ebeef5; border-radius: 4px; padding: 6px 8px; margin-bottom: 8px; }
.field-head { display: flex; justify-content: space-between; align-items: center; }
.samples { display: flex; flex-wrap: wrap; gap: 4px; margin: 4px 0; }
.sample { background: #f4f4f5; border-radius: 3px; padding: 1px 6px; font-size: 12px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row { display: flex; align-items: center; gap: 6px; margin-top: 6px; }
</style>
