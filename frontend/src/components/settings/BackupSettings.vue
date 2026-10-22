<template>
  <el-card shadow="never">
    <template #header>
      <div class="card-title">
        <span>数据备份 <span class="muted">每天定时 pg_dump，保留最近 N 天（在 .env 中设置 BACKUP_TIME / BACKUP_KEEP_DAYS）</span></span>
        <el-button type="primary" :disabled="!data.enabled" :loading="data.pending" @click="backupNow">立即备份</el-button>
      </div>
    </template>
    <el-alert v-if="!data.enabled" type="warning" :closable="false" show-icon
      title="未检测到备份目录：请使用 docker compose 部署（包含 backup 服务），或设置 BACKUP_DIR" />
    <template v-else>
      <el-alert v-if="data.status" :type="data.status.ok ? 'success' : 'error'" :closable="false" show-icon style="margin-bottom: 12px"
        :title="data.status.ok ? `最近一次备份成功：${data.status.file}（${fmtTime(data.status.at)}，耗时 ${data.status.seconds}s）` : `最近一次备份失败（${fmtTime(data.status.at)}）：${data.status.error}`" />
      <el-table :data="data.files" size="small">
        <el-table-column label="文件" prop="name" />
        <el-table-column label="大小" width="120" align="right"><template #default="{ row }">{{ size(row.size) }}</template></el-table-column>
        <el-table-column label="时间" width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column width="90"><template #default="{ row }"><el-button link type="primary" @click="download(row)">下载</el-button></template></el-table-column>
      </el-table>
      <div class="muted restore">恢复方法：<code>gunzip -c 备份文件.sql.gz | docker compose exec -T postgres psql -U collect -d collect</code>（建议先恢复到新库核对）</div>
    </template>
  </el-card>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { downloadBackup, listBackups, requestBackup } from '../../api'
import { fmtTime, saveBlob } from '../../format'

const data = ref({ enabled: true, files: [], status: null, pending: false })
let timer

const size = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`)

async function load() {
  data.value = await listBackups()
  clearTimeout(timer)
  if (data.value.pending) timer = setTimeout(load, 3000)
}

async function backupNow() {
  ElMessage.success((await requestBackup()).message)
  load()
}

async function download(row) {
  saveBlob(await downloadBackup(row.name), row.name)
}

onMounted(load)
onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.restore { margin-top: 12px; }
code { background: #f1f5f9; padding: 2px 6px; border-radius: 4px; }
</style>
