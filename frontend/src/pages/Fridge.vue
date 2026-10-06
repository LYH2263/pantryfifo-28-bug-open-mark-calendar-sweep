<template>
  <div>
    <h1>冰箱分层</h1>
    <p class="muted">竖列分层 · FEFO 消费走「消费」页 · 开封角标 · 点批上「开封」钉住剩余小时</p>
    <div class="fridge">
      <section v-for="L in layers" :key="L" class="shelf">
        <h3>{{ label[L] }}</h3>
        <LotCard v-for="x in by(L)" :key="x.id" :lot="x" />
      </section>
    </div>
    <button style="margin-top:12px" @click="sweep">过期/开封超时下架</button>
  </div>
</template>
<script setup>
import { computed, onMounted } from 'vue'
import { store, refreshFridge } from '../store'
import { api } from '../api'
import LotCard from '../components/LotCard.vue'
const layers = ['upper','mid','lower']
const label = { upper: '上层', mid: '中层', lower: '下层' }
const by = (L) => store.lots.filter(r => r.layer === L)
async function load() { await refreshFridge() }
async function sweep() {
  await api('/expire-sweep', { method: 'POST', body: '{}' })
  await load()
}
onMounted(load)
</script>
