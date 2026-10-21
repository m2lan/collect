<template>
  <div class="cron">
    <el-select :model-value="preset" :disabled="disabled" style="width: 180px" @change="onPreset">
      <el-option v-for="p in PRESETS" :key="p.value" :label="p.label" :value="p.value" />
      <el-option label="自定义 cron" value="custom" />
    </el-select>
    <el-input :model-value="modelValue" :disabled="disabled" placeholder="分 时 日 月 周" style="width: 180px"
      @update:model-value="emit('update:modelValue', $event)" />
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ modelValue: String, disabled: Boolean, presets: Array })
const emit = defineEmits(['update:modelValue'])

const DEFAULT_PRESETS = [
  { label: '每 10 分钟', value: '*/10 * * * *' },
  { label: '每 30 分钟', value: '*/30 * * * *' },
  { label: '每小时', value: '0 * * * *' },
  { label: '每 2 小时', value: '0 */2 * * *' },
  { label: '每 6 小时', value: '0 */6 * * *' },
  { label: '每天 8:00', value: '0 8 * * *' },
  { label: '每天 8:00 和 20:00', value: '0 8,20 * * *' },
]
const PRESETS = computed(() => props.presets || DEFAULT_PRESETS)
const preset = computed(() => (PRESETS.value.some((p) => p.value === props.modelValue) ? props.modelValue : 'custom'))

function onPreset(v) {
  if (v !== 'custom') emit('update:modelValue', v)
}
</script>

<style scoped>
.cron { display: flex; gap: 8px; }
</style>
