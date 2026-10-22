<template>
  <el-container class="shell">
    <el-aside :width="collapsed ? '68px' : '220px'" class="aside">
      <div class="brand" @click="router.push('/dashboard')">
        <div class="logo"><el-icon><TrendCharts /></el-icon></div>
        <span v-show="!collapsed">拍卖价格采集</span>
      </div>
      <nav class="nav">
        <template v-for="g in menu" :key="g.group">
          <div v-show="!collapsed" class="group">{{ g.group }}</div>
          <router-link v-for="m in g.items" :key="m.path" :to="m.path" class="item" :class="{ active: route.path.startsWith(m.path) }"
            :title="m.title">
            <el-icon><component :is="ICONS[m.icon]" /></el-icon>
            <span v-show="!collapsed">{{ m.title }}</span>
          </router-link>
        </template>
      </nav>
      <div class="collapse" @click="collapsed = !collapsed">
        <el-icon><component :is="collapsed ? Expand : Fold" /></el-icon>
      </div>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div class="crumb">{{ route.meta.title }}</div>
        <el-dropdown trigger="click" @command="onCommand">
          <div class="user">
            <el-avatar :size="30" class="avatar">{{ (auth.user?.display_name || '?').slice(0, 1).toUpperCase() }}</el-avatar>
            <div class="who">
              <div class="name">{{ auth.user?.display_name }}</div>
              <div class="role">{{ auth.user?.role_label }}</div>
            </div>
            <el-icon><ArrowDown /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="password" :icon="Lock">修改密码</el-dropdown-item>
              <el-dropdown-item command="logout" :icon="SwitchButton" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="main"><router-view /></el-main>
    </el-container>
  </el-container>

  <el-dialog v-model="pwdVisible" title="修改密码" width="420px">
    <el-form :model="pwd" label-width="80px">
      <el-form-item label="原密码"><el-input v-model="pwd.old_password" type="password" show-password /></el-form-item>
      <el-form-item label="新密码"><el-input v-model="pwd.new_password" type="password" show-password placeholder="至少 6 位" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="pwdVisible = false">取消</el-button>
      <el-button type="primary" @click="savePassword">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowDown, Bell, DataBoard, Document, Expand, Fold, Goods, Link, Lock, Setting, SwitchButton, TrendCharts,
} from '@element-plus/icons-vue'
import { changePassword } from '../api'
import { MENU } from '../router'
import { auth, can } from '../store'

const ICONS = { DataBoard, Goods, TrendCharts, Link, Document, Bell, Setting }
const route = useRoute()
const router = useRouter()
const collapsed = ref(localStorage.getItem('menu-collapsed') === '1')
const pwdVisible = ref(false)
const pwd = ref({ old_password: '', new_password: '' })

const menu = computed(() =>
  MENU.map((g) => ({ ...g, items: g.items.filter((m) => !m.role || can(m.role)) })).filter((g) => g.items.length),
)

function onCommand(cmd) {
  if (cmd === 'logout') {
    localStorage.removeItem('token')
    auth.user = null
    router.push({ name: 'login' })
  } else {
    pwd.value = { old_password: '', new_password: '' }
    pwdVisible.value = true
  }
}

async function savePassword() {
  await changePassword(pwd.value)
  ElMessage.success('密码已修改')
  pwdVisible.value = false
}

watch(collapsed, (v) => localStorage.setItem('menu-collapsed', v ? '1' : '0'))
</script>

<style scoped>
.shell { height: 100%; }
.aside { background: linear-gradient(180deg, var(--c-side), var(--c-side-2)); display: flex; flex-direction: column; transition: width .2s; overflow: hidden; }
.brand { display: flex; align-items: center; gap: 10px; height: 60px; padding: 0 18px; color: #fff; font-weight: 650; font-size: 16px; cursor: pointer; white-space: nowrap; }
.logo { width: 32px; height: 32px; flex: none; border-radius: 9px; display: grid; place-items: center; background: linear-gradient(135deg, #3b82f6, #6366f1); font-size: 18px; }
.nav { flex: 1; padding: 6px 10px; overflow-y: auto; }
.group { color: #64748b; font-size: 11px; letter-spacing: .08em; padding: 14px 10px 6px; }
.item { display: flex; align-items: center; gap: 10px; padding: 9px 12px; margin: 2px 0; border-radius: 8px; color: #cbd5e1; text-decoration: none; font-size: 14px; white-space: nowrap; }
.item:hover { background: rgba(255, 255, 255, .06); color: #fff; }
.item.active { background: var(--c-primary); color: #fff; box-shadow: 0 4px 12px rgba(37, 99, 235, .35); }
.item .el-icon { font-size: 17px; }
.collapse { padding: 14px 24px; color: #64748b; cursor: pointer; }
.collapse:hover { color: #fff; }
.header { display: flex; align-items: center; justify-content: space-between; background: rgba(255, 255, 255, .85); backdrop-filter: blur(8px); border-bottom: 1px solid var(--c-border); height: 60px; }
.crumb { font-weight: 600; font-size: 15px; }
.user { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.avatar { background: linear-gradient(135deg, #3b82f6, #6366f1); font-weight: 600; }
.who { line-height: 1.2; }
.name { font-size: 13px; font-weight: 600; }
.role { font-size: 11px; color: var(--c-muted); }
.main { padding: 20px 24px 32px; }
</style>
