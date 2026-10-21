<template>
  <el-card>
    <div class="page-header">
      <span class="muted">添加拍卖平台的列表页链接并配置采集规则</span>
      <el-button type="primary" :icon="Plus" @click="openDialog()">新增站点</el-button>
    </div>
    <el-table :data="rows" v-loading="loading">
      <el-table-column label="名称" prop="name" min-width="140" />
      <el-table-column label="链接" min-width="240">
        <template #default="{ row }"><el-link :href="row.start_url" target="_blank">{{ row.start_url }}</el-link></template>
      </el-table-column>
      <el-table-column label="抓取方式" width="100">
        <template #default="{ row }">{{ row.fetch_mode === 'browser' ? '浏览器' : '静态' }}</template>
      </el-table-column>
      <el-table-column label="登录" width="90">
        <template #default="{ row }">{{ AUTH_LABEL[row.auth_type] }}</template>
      </el-table-column>
      <el-table-column label="规则" width="70">
        <template #default="{ row }">{{ row.active_rule_version ? `v${row.active_rule_version}` : '未配置' }}</template>
      </el-table-column>
      <el-table-column label="拍品数" prop="lot_count" width="80" />
      <el-table-column label="最近采集" width="170">
        <template #default="{ row }">
          <template v-if="row.last_run">
            <el-tag :type="RUN_STATUS[row.last_run.status]?.type" size="small">{{ RUN_STATUS[row.last_run.status]?.label }}</el-tag>
            <span class="muted"> {{ fmtTime(row.last_run.created_at) }}</span>
          </template>
          <span v-else class="muted">从未采集</span>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="70">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '是' : '否' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="!row.active_rule_version" @click="crawl(row)">立即采集</el-button>
          <el-button link type="primary" @click="openDialog(row)">编辑</el-button>
          <el-button link @click="router.push({ name: 'runs', query: { source_id: row.id } })">记录</el-button>
          <el-popconfirm title="删除站点会同时删除其拍品数据，确定？" @confirm="remove(row)">
            <template #reference><el-button link type="danger">删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <SourceDialog v-model="dialogVisible" :source="editing" @saved="load" />
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { crawlSource, deleteSource, listSources } from '../api'
import { RUN_STATUS, fmtTime } from '../format'
import SourceDialog from '../components/SourceDialog.vue'

const AUTH_LABEL = { none: '无需登录', cookie: 'Cookie', form: '账号密码' }
const router = useRouter()
const rows = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref(null)

async function load() {
  loading.value = true
  try {
    rows.value = await listSources()
  } finally {
    loading.value = false
  }
}

function openDialog(row = null) {
  editing.value = row
  dialogVisible.value = true
}

async function crawl(row) {
  const run = await crawlSource(row.id)
  ElMessage.success(`已创建采集任务 #${run.id}`)
  load()
}

async function remove(row) {
  await deleteSource(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(load)
</script>
