import { reactive } from 'vue'
import { api } from './api'

// 顶条与层页角标的唯一数据来源：一次 /fridge 请求带回服务端 assess_lot
// 对每批的单次判定（level/reason_code/reason），顶条只是过滤同一份数组，
// 不再另发请求，保证“原因字与角标属于同一次判定”。
export const store = reactive({
  lots: [],
  alerts: [],
  loaded: false,
})

export async function refreshFridge() {
  const rows = await api('/fridge')
  store.lots = rows
  store.alerts = rows.filter(r => r.level === 'expired' || r.level === 'soon')
  store.loaded = true
  return rows
}
