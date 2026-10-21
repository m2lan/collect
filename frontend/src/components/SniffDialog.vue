<template>
  <el-dialog :model-value="modelValue" title="嗅探数据接口" width="900px" :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)" @open="load">
    <div v-loading="loading" element-loading-text="正在用浏览器打开页面并捕获接口…" style="min-height: 160px">
      <template v-if="result">
        <div class="muted" style="margin-bottom: 8px">
          共捕获 {{ result.captured }} 个 JSON 响应，其中 {{ result.candidates.length }} 个包含列表数据。
          使用接口采集比解析网页更快更稳定。
        </div>
        <el-empty v-if="!result.candidates.length" description="没有找到返回拍品列表的接口，请使用网页规则" />
        <el-card v-for="(c, i) in result.candidates" :key="i" shadow="never" class="cand">
          <div class="cand-head">
            <div>
              <el-tag disable-transitions size="small">{{ c.method }}</el-tag>
              <span class="url">{{ c.url }}</span>
            </div>
            <el-button type="primary" size="small" @click="use(c)">使用此接口</el-button>
          </div>
          <div class="muted">拍品数组：<code>{{ c.items_path }}</code>，本页 {{ c.count }} 条；识别字段：
            <el-tag disable-transitions v-for="f in c.guessed_fields" :key="f.key" size="small" type="info" style="margin-right: 4px">{{ f.key }} ← {{ f.selector }}</el-tag>
          </div>
          <el-collapse>
            <el-collapse-item title="第一条数据样本">
              <pre class="sample">{{ JSON.stringify(c.sample, null, 2) }}</pre>
            </el-collapse-item>
          </el-collapse>
        </el-card>
      </template>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { sniff } from '../api'

const props = defineProps({ modelValue: Boolean, request: Object })
const emit = defineEmits(['update:modelValue', 'done'])
const loading = ref(false)
const result = ref(null)

async function load() {
  result.value = null
  loading.value = true
  try {
    result.value = await sniff(props.request)
  } catch {
    emit('update:modelValue', false)
  } finally {
    loading.value = false
  }
}

function use(c) {
  emit('done', c.rule)
  emit('update:modelValue', false)
}
</script>

<style scoped>
.cand { margin-bottom: 8px; }
.cand-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.url { margin-left: 6px; word-break: break-all; font-family: Menlo, Consolas, monospace; font-size: 12px; }
.sample { max-height: 240px; overflow: auto; font-size: 12px; background: #f5f7fa; padding: 8px; margin: 0; }
</style>
