<template>
  <el-card shadow="never">
    <template #header>搜索同义词</template>
    <p class="muted">每行一组同义词，用逗号、顿号或空格分隔。搜索其中任一个词时，会同时匹配同组的其他词。</p>
    <el-input v-model="synonyms" type="textarea" :rows="10" class="mono" placeholder="翡翠,玉翠,缅甸玉&#10;劳力士,rolex" />
    <el-button type="primary" style="margin-top: 12px" @click="save">保存</el-button>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getSearchSettings, putSearchSettings } from '../../api'

const synonyms = ref('')

async function save() {
  synonyms.value = (await putSearchSettings({ synonyms: synonyms.value })).synonyms
  ElMessage.success('已保存')
}

onMounted(async () => {
  synonyms.value = (await getSearchSettings()).synonyms
})
</script>
