<template>
  <el-card shadow="never">
    <template #header>操作日志 <span class="muted">记录所有写操作与登录</span></template>
    <div class="toolbar">
      <el-input v-model="q.username" placeholder="用户名" clearable style="width: 160px" @keyup.enter="search" @clear="search" />
      <el-input v-model="q.action" placeholder="操作，如 删除 / 登录" clearable style="width: 200px" @keyup.enter="search" @clear="search" />
      <el-button type="primary" @click="search">查询</el-button>
    </div>
    <el-table :data="rows" size="small">
      <el-table-column label="时间" width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
      <el-table-column label="用户" prop="username" width="120" />
      <el-table-column label="操作" prop="action" min-width="140" />
      <el-table-column label="结果" width="90">
        <template #default="{ row }"><el-tag disable-transitions size="small" :type="row.status_code < 400 ? 'success' : 'danger'">{{ row.status_code < 400 ? '成功' : row.status_code }}</el-tag></template>
      </el-table-column>
      <el-table-column label="请求" min-width="220"><template #default="{ row }"><span class="muted">{{ row.method }} {{ row.path }}</span></template></el-table-column>
      <el-table-column label="IP" prop="ip" width="140" />
    </el-table>
    <el-pagination class="pager" layout="total, prev, pager, next" :total="total" v-model:current-page="q.page" :page-size="20" @current-change="load" />
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { listAudit } from '../../api'
import { fmtTime } from '../../format'

const q = reactive({ username: '', action: '', page: 1 })
const rows = ref([])
const total = ref(0)

async function load() {
  const params = Object.fromEntries(Object.entries({ ...q, page_size: 20 }).filter(([, v]) => v !== ''))
  const r = await listAudit(params)
  rows.value = r.items
  total.value = r.total
}

function search() {
  q.page = 1
  load()
}

onMounted(load)
</script>

<style scoped>
.pager { margin-top: 12px; justify-content: flex-end; }
</style>
