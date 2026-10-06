<template>
  <div>
    <h1>设置 · 开封角标</h1>
    <label class="setting-row">
      默认开封剩余小时（仅对之后开封的批生效；已开封批仍按开封时钉住的小时走）
      <input type="number" min="1" step="1" v-model.number="openHours" />
    </label>
    <button @click="save">保存设置</button>
    <p v-if="msg" class="muted">{{ msg }}</p>
    <pre>{{ JSON.stringify(s, null, 2) }}</pre>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const s = ref({})
const openHours = ref(48)
const msg = ref('')
async function load() {
  s.value = await api('/settings')
  if (s.value.default_open_hours != null) openHours.value = Number(s.value.default_open_hours)
}
async function save() {
  s.value = await api('/settings', {
    method: 'PUT',
    body: JSON.stringify({ default_open_hours: openHours.value }),
  })
  msg.value = '已保存：已开封（含已超时待下架）的批不会被回溯'
}
onMounted(load)
</script>
