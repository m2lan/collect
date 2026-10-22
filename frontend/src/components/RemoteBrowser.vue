<template>
  <el-dialog :model-value="modelValue" :title="`远程登录 - ${source?.name || ''}`" width="1340px" top="3vh"
    :close-on-click-modal="false" destroy-on-close @update:model-value="emit('update:modelValue', $event)"
    @open="start" @closed="stop">
    <el-alert type="info" :closable="false" show-icon class="tip"
      title="下面是服务器上的真实浏览器。像平时一样点击、输入，完成扫码 / 验证码 / 短信登录或人机验证，然后点击「保存登录状态」。之后的采集会复用这个会话。" />
    <div class="bar">
      <el-button :icon="Back" circle size="small" :disabled="!sid" @click="act({ type: 'back' })" />
      <el-button :icon="Refresh" circle size="small" :disabled="!sid" @click="act({ type: 'reload' })" />
      <el-input v-model="address" size="small" class="address" @keyup.enter="act({ type: 'navigate', url: address })">
        <template #prefix><el-icon><Link /></el-icon></template>
      </el-input>
      <el-input v-model="text" size="small" class="text" placeholder="输入文字后回车发送到浏览器（中文、账号、验证码）"
        @keyup.enter="sendText" />
      <el-button size="small" :disabled="!text" @click="sendText">发送</el-button>
    </div>
    <div ref="screen" class="screen" tabindex="0"
      @keydown.prevent="onKey" @wheel.prevent="onWheel">
      <img v-if="frame" :src="frame" draggable="false" @click="onClick" @dblclick="onDblClick" />
      <div v-else class="starting"><el-icon class="is-loading"><Loading /></el-icon>正在启动浏览器…</div>
    </div>
    <div class="muted foot">
      {{ title || '—' }} · 点击画面后可直接用键盘输入英文和数字；中文请用上方输入框。会话空闲 10 分钟自动关闭。
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
      <el-button type="primary" :loading="saving" :disabled="!sid" @click="save">保存登录状态</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Back, Link, Loading, Refresh } from '@element-plus/icons-vue'
import http from '../api/http'

const props = defineProps({ modelValue: Boolean, source: Object, url: String })
const emit = defineEmits(['update:modelValue', 'saved'])

const sid = ref(null)
const frame = ref(null)
const address = ref('')
const title = ref('')
const text = ref('')
const saving = ref(false)
const screen = ref(null)
let viewport = { width: 1280, height: 800 }
let timer = null
let alive = false
let ticks = 0

const KEYS = new Set(['Enter', 'Backspace', 'Tab', 'Escape', 'Delete', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'])

async function start() {
  alive = true
  frame.value = null
  try {
    const r = await http.post('/remote/start', { source_id: props.source.id, url: props.url || null }, { timeout: 90000 })
    sid.value = r.session_id
    address.value = r.url
    title.value = r.title
    viewport = r.viewport
    poll()
  } catch {
    emit('update:modelValue', false)
  }
}

async function poll() {
  if (!alive || !sid.value) return
  try {
    const blob = await http.get(`/remote/${sid.value}/screenshot`, { responseType: 'blob', timeout: 20000 })
    const old = frame.value
    frame.value = URL.createObjectURL(blob)
    if (old) URL.revokeObjectURL(old)
  } catch {
    // 截图偶尔失败（页面跳转中）时忽略，下一轮继续
  }
  // 页面可能自行跳转（如登录成功后），定时同步地址栏；用户正在编辑地址时不覆盖
  if (++ticks % 3 === 0 && document.activeElement?.closest?.('.address') == null) {
    try {
      const st = await http.get(`/remote/${sid.value}/state`)
      address.value = st.url
      title.value = st.title
    } catch {
      // 忽略
    }
  }
  timer = setTimeout(poll, 700)
}

async function act(action) {
  if (!sid.value) return
  try {
    const r = await http.post(`/remote/${sid.value}/action`, action, { timeout: 70000 })
    address.value = r.url
    title.value = r.title
  } catch {
    // 错误已提示
  }
}

function point(e) {
  const img = e.target
  return {
    x: Math.round((e.offsetX * viewport.width) / img.clientWidth),
    y: Math.round((e.offsetY * viewport.height) / img.clientHeight),
  }
}

function onClick(e) {
  screen.value?.focus()
  act({ type: 'click', ...point(e) })
}

function onDblClick(e) {
  act({ type: 'dblclick', ...point(e) })
}

function onWheel(e) {
  act({ type: 'scroll', dx: e.deltaX, dy: e.deltaY })
}

function onKey(e) {
  if (e.ctrlKey || e.metaKey) {
    if (e.key.toLowerCase() === 'v') navigator.clipboard?.readText().then((t) => t && act({ type: 'type', text: t }))
    return
  }
  if (KEYS.has(e.key)) act({ type: 'key', key: e.key })
  else if (e.key.length === 1) act({ type: 'type', text: e.key })
}

function sendText() {
  if (!text.value) return
  act({ type: 'type', text: text.value })
  text.value = ''
}

async function save() {
  saving.value = true
  try {
    const r = await http.post(`/remote/${sid.value}/save`)
    ElMessage.success(`已保存登录状态（${r.cookies} 个 Cookie），之后的采集将复用该会话`)
    emit('saved')
    emit('update:modelValue', false)
  } finally {
    saving.value = false
  }
}

async function stop() {
  alive = false
  clearTimeout(timer)
  if (sid.value) http.delete(`/remote/${sid.value}`).catch(() => {})
  sid.value = null
  if (frame.value) URL.revokeObjectURL(frame.value)
  frame.value = null
}

onBeforeUnmount(stop)
</script>

<style scoped>
.tip { margin-bottom: 10px; }
.bar { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.address { flex: 1.2; }
.text { flex: 1; }
.screen { position: relative; width: 100%; aspect-ratio: 1280 / 800; background: #0f172a; border-radius: 8px; overflow: hidden; outline: none; }
.screen:focus { box-shadow: 0 0 0 2px var(--el-color-primary); }
.screen img { width: 100%; height: 100%; display: block; cursor: pointer; user-select: none; }
.starting { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; gap: 8px; color: #cbd5e1; }
.foot { margin-top: 6px; }
</style>
