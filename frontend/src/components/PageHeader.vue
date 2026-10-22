<template>
  <div class="page-header">
    <div>
      <h2>{{ title }}</h2>
      <p v-if="desc">{{ desc }}</p>
    </div>
    <div class="actions"><slot /></div>
  </div>
</template>

<script setup>
defineProps({ title: String, desc: String })
</script>
