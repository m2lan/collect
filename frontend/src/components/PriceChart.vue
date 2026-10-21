<template>
  <div ref="el" class="chart" />
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { fmtPrice, fmtTime } from '../format'

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({ snapshots: Array, currency: String })
const el = ref(null)
let chart
let observer

function render() {
  if (!chart) return
  const points = (props.snapshots || []).filter((s) => s.price !== null).map((s) => [new Date(s.captured_at).getTime(), Number(s.price), s.bid_count])
  chart.setOption({
    grid: { left: 64, right: 16, top: 16, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      formatter: (items) => {
        const [t, price, bids] = items[0].value
        return `${fmtTime(t)}<br/>价格：${fmtPrice(price, props.currency)}${bids != null ? `<br/>出价：${bids} 次` : ''}`
      },
    },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', scale: true, axisLabel: { formatter: (v) => (v >= 10000 ? `${v / 10000}万` : v) } },
    series: [{ type: 'line', step: 'end', data: points, symbolSize: 6, areaStyle: { opacity: 0.08 } }],
  })
}

onMounted(() => {
  chart = echarts.init(el.value)
  render()
  // 抽屉打开动画期间容器宽度会变化
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(el.value)
})
watch(() => props.snapshots, render)
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<style scoped>
.chart { width: 100%; height: 240px; }
</style>
