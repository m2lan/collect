<template>
  <div class="page">
    <PageHeader title="系统设置" desc="AI、采集与反爬、通知、搜索、模板、备份、用户与日志" />
    <el-tabs v-model="tab" tab-position="left" class="settings">
      <el-tab-pane v-for="t in TABS" :key="t.name" :label="t.label" :name="t.name" lazy>
        <component :is="t.component" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'
import AISettings from '../components/settings/AISettings.vue'
import AlertSettings from '../components/settings/AlertSettings.vue'
import AuditLog from '../components/settings/AuditLog.vue'
import BackupSettings from '../components/settings/BackupSettings.vue'
import CrawlerSettings from '../components/settings/CrawlerSettings.vue'
import SearchSettings from '../components/settings/SearchSettings.vue'
import TemplateSettings from '../components/settings/TemplateSettings.vue'
import UserSettings from '../components/settings/UserSettings.vue'

const TABS = [
  { name: 'ai', label: 'AI 功能与模型', component: AISettings },
  { name: 'crawler', label: '采集与反爬', component: CrawlerSettings },
  { name: 'alert', label: '告警通知', component: AlertSettings },
  { name: 'search', label: '搜索同义词', component: SearchSettings },
  { name: 'templates', label: '规则模板', component: TemplateSettings },
  { name: 'backup', label: '数据备份', component: BackupSettings },
  { name: 'users', label: '用户管理', component: UserSettings },
  { name: 'audit', label: '操作日志', component: AuditLog },
]
const route = useRoute()
const router = useRouter()
const tab = ref(TABS.some((t) => t.name === route.query.tab) ? route.query.tab : 'ai')
watch(tab, (t) => router.replace({ query: { tab: t } }))
</script>

<style scoped>
.settings :deep(.el-tabs__header.is-left) { margin-right: 20px; }
.settings :deep(.el-tabs__item.is-left) { justify-content: flex-start; min-width: 150px; height: 42px; }
</style>
