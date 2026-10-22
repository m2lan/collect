<template>
  <el-card shadow="never">
    <template #header>采集与反爬</template>
    <el-form :model="c" label-width="160px" style="max-width: 820px">
      <el-divider content-position="left">反爬</el-divider>
      <el-form-item label="浏览器指纹伪装">
        <el-switch v-model="c.stealth" />
        <span class="muted tip">隐藏无头浏览器特征（webdriver、插件、WebGL 等），降低被识别为机器人的概率</span>
      </el-form-item>
      <el-form-item label="随机 User-Agent">
        <el-switch v-model="c.random_ua" />
        <span class="muted tip">每次采集随机使用常见浏览器 UA；人工登录的站点固定使用登录时的 UA</span>
      </el-form-item>
      <el-form-item label="按域名限速">
        <div>同一主域名的请求至少间隔 <el-input-number v-model="c.domain_interval" :min="0" :max="60" :step="0.5" :precision="1" style="margin: 0 8px; width: 120px" /> 秒</div>
        <div class="muted" style="width: 100%">跨站点、跨任务共享（例如多个淘宝搜索站点一起排队），0 表示不限</div>
      </el-form-item>
      <el-form-item label="单次采集时长上限">
        <div>超过 <el-input-number v-model="c.max_run_minutes" :min="1" :max="1440" style="margin: 0 8px; width: 120px" /> 分钟停止翻页，已抓到的数据照常保存</div>
        <div class="muted" style="width: 100%">防止网站卡住时任务一直占用；单个页面另有加载时限，卡住会自动重试并跳过</div>
      </el-form-item>
      <el-form-item label="代理池">
        <el-input v-model="proxies" type="textarea" :rows="4" class="mono" placeholder="每行一个，如：&#10;http://user:pass@1.2.3.4:8080&#10;socks5://5.6.7.8:1080" />
        <div class="muted">站点开启“使用代理”后，每次采集随机选择一个</div>
      </el-form-item>
      <el-divider content-position="left">临近结束加密盯价</el-divider>
      <el-form-item label="加密时间窗口">
        结束前 <el-input-number v-model="c.boost_window_minutes" :min="1" :max="1440" style="margin: 0 8px; width: 120px" /> 分钟内
      </el-form-item>
      <el-form-item label="刷新间隔">
        <div>每 <el-input-number v-model="c.boost_interval_minutes" :min="0.5" :max="60" :step="0.5" :precision="1" style="margin: 0 8px; width: 120px" /> 分钟刷新一次</div>
        <div class="muted" style="width: 100%">只对开启了“加密盯价”的站点生效，只刷新临近结束的拍品</div>
      </el-form-item>
      <el-form-item><el-button type="primary" @click="save">保存</el-button></el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getCrawlerSettings, putCrawlerSettings } from '../../api'

const c = ref({})
const proxies = ref('')

async function load() {
  c.value = await getCrawlerSettings()
  proxies.value = c.value.proxies.join('\n')
}

async function save() {
  c.value = await putCrawlerSettings({ ...c.value, proxies: proxies.value.split('\n') })
  proxies.value = c.value.proxies.join('\n')
  ElMessage.success('已保存')
}

onMounted(load)
</script>

<style scoped>
.tip { margin-left: 12px; }
</style>
