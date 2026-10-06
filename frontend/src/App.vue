<template>
  <div>
    <div class="alert-bar" v-if="store.alerts.length">
      临期预警：
      <span v-for="a in store.alerts" :key="a.id" class="alert-chip" :class="a.level">
        {{ a.name }}<b>（{{ a.reason }}）</b><template v-if="a.level==='expired'">待下架</template>
      </span>
    </div>
    <div class="alert-bar ok" v-else>临期预警带：暂无紧急批次</div>
    <div class="wrap">
      <nav class="layer-tabs">
        <router-link to="/">全层</router-link>
        <router-link to="/layer/upper">上层</router-link>
        <router-link to="/layer/mid">中层</router-link>
        <router-link to="/layer/lower">下层</router-link>
        <router-link to="/inbound">入库</router-link>
        <router-link to="/consume">消费</router-link>
        <router-link to="/settings">设置</router-link>
      </nav>
      <router-view />
    </div>
  </div>
</template>
<script setup>
import { onMounted, onUnmounted } from 'vue'
import { store, refreshFridge } from './store'
onMounted(async () => {
  try { await refreshFridge() } catch {}
  // 开封剩余小时随时间流逝：定时用服务端同一份判定刷新顶条与角标
  timer = setInterval(() => refreshFridge().catch(() => {}), 30000)
})
let timer
onUnmounted(() => clearInterval(timer))
</script>
