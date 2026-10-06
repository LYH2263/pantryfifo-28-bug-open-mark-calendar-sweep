<template>
  <div>
    <h1>{{ props.layer }} 层 · 开封角标</h1>
    <p class="muted" v-if="!rows.length">该层暂无在架批次</p>
    <LotCard v-for="x in rows" :key="x.id" :lot="x" />
  </div>
</template>
<script setup>
import { computed, watch, onMounted } from 'vue'
import { store, refreshFridge } from '../store'
import LotCard from '../components/LotCard.vue'
const props = defineProps({ layer: String })
const rows = computed(() => store.lots.filter(r => r.layer === props.layer))
async function load() { await refreshFridge() }
watch(() => props.layer, load)
onMounted(load)
</script>
