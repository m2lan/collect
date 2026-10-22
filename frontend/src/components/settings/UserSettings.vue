<template>
  <el-card shadow="never">
    <template #header>
      <div class="card-title">
        <span>用户管理 <span class="muted">管理员：全部功能；操作员：站点、规则、采集、订阅；只读：查看数据</span></span>
        <el-button type="primary" :icon="Plus" @click="open()">添加用户</el-button>
      </div>
    </template>
    <el-table :data="users" size="default">
      <el-table-column label="用户名" prop="username" min-width="120" />
      <el-table-column label="显示名" prop="display_name" min-width="120" />
      <el-table-column label="角色" width="100"><template #default="{ row }"><el-tag disable-transitions :type="ROLE_TYPE[row.role]" size="small">{{ ROLE[row.role] }}</el-tag></template></el-table-column>
      <el-table-column label="状态" width="80"><template #default="{ row }"><el-tag disable-transitions :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag></template></el-table-column>
      <el-table-column label="最近登录" width="160"><template #default="{ row }">{{ fmtTime(row.last_login_at) }}</template></el-table-column>
      <el-table-column label="创建时间" width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
      <el-table-column width="130">
        <template #default="{ row }">
          <el-button link type="primary" @click="open(row)">编辑</el-button>
          <el-popconfirm v-if="row.id !== auth.user?.id" title="确定删除该用户？" @confirm="remove(row)">
            <template #reference><el-button link type="danger">删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="visible" :title="editing ? '编辑用户' : '添加用户'" width="480px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="用户名" required><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="显示名"><el-input v-model="form.display_name" /></el-form-item>
        <el-form-item label="角色">
          <el-radio-group v-model="form.role">
            <el-radio v-for="(v, k) in ROLE" :key="k" :value="k">{{ v }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="editing ? '重置密码' : '初始密码'" :required="!editing">
          <el-input v-model="form.password" type="password" show-password :placeholder="editing ? '留空不修改' : '至少 6 位'" />
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { createUser, deleteUser, listUsers, updateUser } from '../../api'
import { fmtTime } from '../../format'
import { auth } from '../../store'

const ROLE = { admin: '管理员', operator: '操作员', viewer: '只读' }
const ROLE_TYPE = { admin: 'danger', operator: 'warning', viewer: 'info' }
const users = ref([])
const visible = ref(false)
const editing = ref(null)
const form = ref({})

async function load() {
  users.value = await listUsers()
}

function open(u = null) {
  editing.value = u
  form.value = u ? { ...u, password: '' } : { username: '', display_name: '', role: 'viewer', is_active: true, password: '' }
  visible.value = true
}

async function save() {
  const body = { ...form.value }
  if (!body.password) delete body.password
  if (editing.value) await updateUser(editing.value.id, body)
  else await createUser(body)
  ElMessage.success('已保存')
  visible.value = false
  load()
}

async function remove(u) {
  await deleteUser(u.id)
  load()
}

onMounted(load)
</script>
