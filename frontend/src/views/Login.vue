<template>
  <div class="login">
    <div class="hero">
      <div class="brand"><div class="logo"><el-icon><TrendCharts /></el-icon></div>拍卖价格采集平台</div>
      <h1>全网拍卖行情，<br />一处掌握。</h1>
      <ul>
        <li>AI 自动生成采集规则，支持扫码登录与接口截获</li>
        <li>新上架、价格变化、即将结束实时推送</li>
        <li>跨平台同款比价与行情分析报告</li>
      </ul>
    </div>
    <div class="panel">
      <el-card class="box" shadow="never">
        <h2>登录</h2>
        <p class="muted">使用管理员分配的账号登录</p>
        <el-form :model="form" size="large" @submit.prevent="submit">
          <el-form-item><el-input v-model="form.username" placeholder="用户名" :prefix-icon="User" /></el-form-item>
          <el-form-item>
            <el-input v-model="form.password" type="password" placeholder="密码" show-password :prefix-icon="Lock" />
          </el-form-item>
          <el-button type="primary" native-type="submit" :loading="loading" class="submit">登录</el-button>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Lock, TrendCharts, User } from '@element-plus/icons-vue'
import { login } from '../api'
import { loadUser } from '../store'

const router = useRouter()
const form = reactive({ username: '', password: '' })
const loading = ref(false)

async function submit() {
  loading.value = true
  try {
    const { access_token } = await login(form)
    localStorage.setItem('token', access_token)
    await loadUser()
    router.push('/')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login { height: 100%; display: grid; grid-template-columns: 1.1fr 1fr; background: var(--c-bg); }
.hero { background: radial-gradient(circle at 20% 20%, #1e3a8a 0, #0b1324 60%); color: #e2e8f0; padding: 56px 64px; display: flex; flex-direction: column; justify-content: center; }
.brand { display: flex; align-items: center; gap: 10px; font-weight: 650; font-size: 18px; color: #fff; position: absolute; top: 40px; }
.logo { width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; background: linear-gradient(135deg, #3b82f6, #6366f1); }
h1 { font-size: 40px; line-height: 1.25; color: #fff; margin: 0 0 24px; letter-spacing: -.02em; }
ul { padding-left: 18px; line-height: 2.1; color: #cbd5e1; }
.panel { display: grid; place-items: center; padding: 24px; }
.box { width: 380px; padding: 12px 8px; }
.box h2 { margin: 0 0 4px; }
.box .muted { margin: 0 0 20px; }
.submit { width: 100%; }
@media (max-width: 900px) { .login { grid-template-columns: 1fr; } .hero { display: none; } }
</style>
