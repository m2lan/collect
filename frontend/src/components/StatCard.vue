<template>
  <div class="stat" :class="tone">
    <div class="label">{{ label }}</div>
    <div class="value num">{{ value ?? '-' }}<span v-if="unit" class="unit">{{ unit }}</span></div>
    <div v-if="sub" class="sub">{{ sub }}</div>
  </div>
</template>

<script setup>
defineProps({ label: String, value: [String, Number], unit: String, sub: String, tone: { type: String, default: '' } })
</script>

<style scoped>
.stat { background: var(--c-card); border: 1px solid var(--c-border); border-radius: var(--radius); padding: 16px 18px; box-shadow: var(--shadow); position: relative; overflow: hidden; }
.stat::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--c-primary); opacity: .8; }
.stat.success::before { background: var(--c-success); }
.stat.warning::before { background: var(--c-warning); }
.stat.danger::before { background: var(--c-danger); }
.label { color: var(--c-text-2); font-size: 13px; }
.value { font-size: 26px; font-weight: 700; margin-top: 4px; letter-spacing: -.02em; }
.unit { font-size: 13px; font-weight: 500; color: var(--c-muted); margin-left: 3px; }
.sub { color: var(--c-muted); font-size: 12px; margin-top: 2px; }
</style>
