<template>
  <el-card shadow="never">
    <template #header>告警通知</template>
    <el-form :model="a" label-width="140px" style="max-width: 760px">
      <el-form-item label="失效判定">
        连续失败 <el-input-number v-model="a.failure_threshold" :min="1" :max="20" style="margin: 0 8px; width: 110px" /> 次判定规则失效
      </el-form-item>
      <el-form-item label="通知方式">
        <el-select v-model="a.webhook_type" style="width: 220px">
          <el-option label="不通知" value="none" />
          <el-option label="企业微信群机器人" value="wecom" />
          <el-option label="钉钉群机器人" value="dingtalk" />
          <el-option label="飞书群机器人" value="feishu" />
          <el-option label="通用 Webhook（JSON）" value="generic" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="a.webhook_type !== 'none'" label="Webhook 地址"><el-input v-model="a.webhook_url" placeholder="https://..." /></el-form-item>
      <el-form-item>
        <el-button type="primary" @click="save">保存</el-button>
        <el-button v-if="a.webhook_type !== 'none'" :loading="testing" @click="test">发送测试消息</el-button>
      </el-form-item>
      <div class="muted">规则失效、登录失效 / 恢复、AI 自动修复、行情报告推送，以及未单独设置渠道的订阅提醒，都会发到这里。</div>
    </el-form>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getAlertSettings, putAlertSettings, testAlert } from '../../api'

const a = ref({ failure_threshold: 2, webhook_type: 'none', webhook_url: '' })
const testing = ref(false)

async function save() {
  a.value = await putAlertSettings(a.value)
  ElMessage.success('已保存')
}

async function test() {
  testing.value = true
  try {
    const r = await testAlert(a.value)
    if (r.ok) ElMessage.success('测试消息已发送')
    else ElMessageBox.alert(r.message, '发送失败', { type: 'error' })
  } finally {
    testing.value = false
  }
}

onMounted(async () => {
  a.value = await getAlertSettings()
})
</script>
