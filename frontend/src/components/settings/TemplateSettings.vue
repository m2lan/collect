<template>
  <div class="stack">
    <el-card shadow="never">
      <template #header>内置平台模板 <span class="muted">经过真实站点验证，新建站点时可直接套用</span></template>
      <el-table :data="builtin" size="small">
        <el-table-column label="名称" prop="name" width="240" />
        <el-table-column label="说明" prop="description" min-width="320" show-overflow-tooltip />
        <el-table-column label="示例链接" min-width="220"><template #default="{ row }"><el-link :href="row.example_url" target="_blank" type="primary">{{ row.example_url }}</el-link></template></el-table-column>
      </el-table>
    </el-card>
    <el-card shadow="never">
      <template #header>自定义模板 <span class="muted">在站点编辑的“采集规则 → 模板 → 保存为模板”中添加</span></template>
      <el-table :data="templates" size="small">
        <el-table-column label="名称" prop="name" min-width="140" />
        <el-table-column label="说明" prop="description" min-width="220" show-overflow-tooltip />
        <el-table-column label="抓取方式" width="100"><template #default="{ row }">{{ MODE[row.config.fetch?.mode] || MODE[row.fetch_mode] }}</template></el-table-column>
        <el-table-column label="字段数" width="80"><template #default="{ row }">{{ row.config.list.fields.length }}</template></el-table-column>
        <el-table-column label="创建时间" width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column width="80">
          <template #default="{ row }">
            <el-popconfirm title="确定删除该模板？" @confirm="remove(row)">
              <template #reference><el-button link type="danger">删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { builtinTemplates, deleteTemplate, listTemplates } from '../../api'
import { fmtTime } from '../../format'

const MODE = { api: '接口', browser: '浏览器', static: '静态' }
const builtin = ref([])
const templates = ref([])

async function remove(row) {
  await deleteTemplate(row.id)
  templates.value = await listTemplates()
}

onMounted(async () => {
  ;[builtin.value, templates.value] = await Promise.all([builtinTemplates(), listTemplates()])
})
</script>

<style scoped>
.stack { display: flex; flex-direction: column; gap: 16px; }
</style>
