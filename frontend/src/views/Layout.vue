<template>
  <el-container style="height: 100%">
    <el-aside width="200px" class="aside">
      <div class="logo">拍卖采集</div>
      <el-menu :default-active="route.path" router background-color="#001529" text-color="#c0c4cc" active-text-color="#fff">
        <el-menu-item index="/lots"><el-icon><Goods /></el-icon>拍品数据</el-menu-item>
        <el-menu-item index="/sources"><el-icon><Link /></el-icon>站点管理</el-menu-item>
        <el-menu-item index="/runs"><el-icon><Document /></el-icon>采集记录</el-menu-item>
        <el-menu-item index="/settings"><el-icon><Setting /></el-icon>系统设置</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span>{{ route.meta.title }}</span>
        <el-dropdown @command="logout">
          <span class="user">{{ user?.username }} <el-icon><ArrowDown /></el-icon></span>
          <template #dropdown>
            <el-dropdown-menu><el-dropdown-item command="logout">退出登录</el-dropdown-item></el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown, Document, Goods, Link, Setting } from '@element-plus/icons-vue'
import { me } from '../api'

const route = useRoute()
const router = useRouter()
const user = ref(null)

onMounted(async () => {
  user.value = await me()
})

function logout() {
  localStorage.removeItem('token')
  router.push({ name: 'login' })
}
</script>

<style scoped>
.aside { background: #001529; }
.aside .el-menu { border-right: none; }
.logo { color: #fff; font-size: 18px; font-weight: 600; padding: 18px 20px; }
.header { display: flex; align-items: center; justify-content: space-between; background: #fff; border-bottom: 1px solid #ebeef5; }
.user { cursor: pointer; display: flex; align-items: center; gap: 4px; }
</style>
