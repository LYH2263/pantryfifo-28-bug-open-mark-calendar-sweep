<template>
  <span class="lot" :class="badgeClass">
    <b>{{ lot.name }}</b> ×{{ lot.qty_remain }} · {{ lot.expiry }}
    <em v-if="lot.opened" class="open-mark">已开封</em>
    <i v-if="lot.opened" class="open-hours" :class="{ over: lot.hours_left < 0 }">
      {{ lot.hours_left >= 0 ? '剩' + fmt(lot.hours_left) + 'h' : '超时' + fmt(-lot.hours_left) + 'h' }}
    </i>
    <b v-if="lot.reason" class="reason-badge" :class="lot.level">{{ lot.reason }}</b>
    <button v-if="!lot.opened" class="open-btn" @click.stop="askOpen">开封</button>

    <span v-if="confirming" class="open-pop">
      将钉住剩余 <b>{{ preview.pinned_open_hours }}</b> 小时，余量保持
      <b>{{ preview.qty_remain }}</b> 不扣减。
      <button @click.stop="confirmOpen">确认开封</button>
      <button class="ghost" @click.stop="cancel">取消</button>
    </span>
    <span v-if="msg" class="open-msg">{{ msg }}</span>
  </span>
</template>
<script setup>
import { ref, computed } from 'vue'
import { api } from '../api'
import { refreshFridge } from '../store'

const props = defineProps({ lot: Object })
const confirming = ref(false)
const preview = ref({})
const msg = ref('')

function fmt(h) { return (Math.round(h * 10) / 10).toString() }
const badgeClass = computed(() => ['lvl-' + props.lot.level, props.lot.opened ? 'is-open' : ''])

async function askOpen() {
  msg.value = ''
  try {
    preview.value = await api(`/lots/${props.lot.id}/open-preview`)
    confirming.value = true
  } catch (e) {
    await onConflict(e)
  }
}

async function confirmOpen() {
  try {
    // 开封确认：服务端只写 opened_at/open_hours，qty_remain 保持
    await api(`/lots/${props.lot.id}/open`, { method: 'POST', body: '{}' })
    confirming.value = false
    // 一次刷新同时更新本批角标与顶条，两者仍属同一份判定
    await refreshFridge()
  } catch (e) {
    confirming.value = false
    await onConflict(e)
  }
}

async function onConflict(e) {
  msg.value = e.message
  await refreshFridge()   // 并发输家：拉回三路写入留下的唯一状态
}

function cancel() { confirming.value = false }
</script>
