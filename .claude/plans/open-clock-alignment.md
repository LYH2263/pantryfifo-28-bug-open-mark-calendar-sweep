# 开封判定对齐：角标 / 紧急条 / 收走 / FEFO 同一世界

## 诊断

`lot_status.assess_lot` 本应是唯一判定源（它自己的 docstring 也这么写），但实现上断成了几截：

1. **`lot_status.py:60` — `open_expired = False` 硬编码**。统一判定永远算不出"开封超时"，
   `hard_expire_reason` 永远不会返回 `OPEN_EXPIRED`。
2. **`open_live.py` 是第二套平行判定**，与 `assess_lot` 口径互相矛盾：
   - `fridge_row` 用**当前默认小时**覆盖批上钉住的 `open_hours` → 改默认开封小时后，未超时批的角标跟新小时走；
   - `alerts_row` 给已开封批强制写 `reason="日历到期"` → 分层页已标开封，紧急条仍按入库到期日走日历字；
   - `sweep_hit` 用 live 默认小时、且对已开封批完全忽略日历截止 → 收走口径与角标、顶条都不在同一次判定上。
3. **FEFO 不过滤硬到期批**：`sort_lots_fefo` 只滤 `qty_remain>0`，已（开封/日历）到期的批不仅可打，
   还因有效截止最早而**最先**被打 → "先到期却还能打到该批"。

种子数据里 lot 7（开封 60h、钉 48h、日历远未到期）现在就是：角标显示 ok/已开封、顶条说日历到期、
sweep 却把它收走、consume 还能打到它 —— 四个世界。

## 修复（全部收敛到 `assess_lot` 一次判定）

### 1. `backend/app/engines/lot_status.py`
- `open_expired = od is not None and now >= od`（核心一行）。
  已有的"两个截止都到时取更早者为原因"仲裁随之生效，语义不变：
  未开封只看日历；已开封看 min(日历截止, opened_at + 钉住的 open_hours)；
  钉住的是**确认当时的剩余小时数**，截止是绝对时间戳，跨自然日天然一致，改默认小时不回溯。

### 2. 删除 `backend/app/engines/open_live.py`
撕裂的根源模块。全仓库只有 `main.py` 引用它（已 grep 确认），删除后全部走 `lot_status`。

### 3. `backend/app/main.py`
- `_annotate`：直接 `assess_lot(lot, now, warn_days)` 挂到批上，去掉 `_live_hours` 注入与 `open_live` 转发；
- `/api/fridge`：不再注入 `_live_hours`；
- `/api/alerts`：改用与 fridge 完全相同的 `_annotate`（顶条与角标字面同一份字段）；
- `/api/expire-sweep`：改用 `hard_expire_reason(lot, now, warn)`（钉住小时 + 日历双截止），不再读默认小时；
- `/api/consume`：FEFO 前用同一 `now` 的 `hard_expire_reason` 剔除硬到期批，结果附 `expired_skipped`（被排除的批 id），
  短货 409 时前端能看出"是先到期被拦下"；
- 清理 import（去掉 `open_live`、未用的 `HARD_EXPIRED`，加入 `hard_expire_reason`）。

开封预览/确认两个端点**不动**：预览本就只读、只报将钉小时；确认本就只写 `opened_at`+钉住 `open_hours`、
不碰 `qty_remain`，且在 `write_tx`（BEGIN IMMEDIATE + 异常回滚）里用条件 UPDATE —— 
"确认失败后余量/角标/紧急条回到开封前"与"开封不扣余量"已由现有结构保证，本次用测试锁死。

### 4. `frontend/src/components/LotCard.vue`（一处小守卫）
- 剩余小时 chip 加 `lot.hours_left != null` 守卫，避免异常行显示 `剩NaNh`。

其余前端不动：`store.js` 本就只用一次 `/fridge` 响应同时喂顶条与层页角标；总表（全层页）与分层页
共用同一 `store.lots` + 同一 `LotCard`，开封确认后 `refreshFridge()` 一次刷新，两处天然一致。

### 5. 新增 `backend/app/tests/test_open_clock_alignment.py`（pytest + TestClient，tmp DATA_DIR 每测试独立库）
- **四处同判**：开封超时批（种子 lot 7）在 /fridge、/alerts 都是 `open_expired`/开封超时/待下架；
  sweep 收走它；consume 打不到它（出现在 `expired_skipped`）；日历到期未开封批同理走 `calendar_expired`；
- **钉住不回溯**：PUT 改 `default_open_hours`（调小也调大）后，已开封批的 `hours_left`、角标 level、
  sweep 结果仍按钉住的 48h；
- **开封不扣余量**：预览前后 qty 不变；确认响应 `qty_unchanged=True`、`open_hours` 为确认时默认值；
  确认后 /fridge 行与确认响应是同一份判定字段（总表=分层页角标）；
- **失败回滚**：重复开封 409 `already_opened` 后，该批余量/角标/开封时刻与之前完全一致；
- **跨自然日同一世界**：直接把库里 `opened_at` 改到昨天深夜（截止在明天凌晨，未超时）→
  fridge/alerts/sweep 三处都判 ok 不收；再改到超时（截止在昨天）→ 三处都判 `open_expired` 都收。

## 验证

- `cd backend && python -m pytest app/tests -q`
- 起 `uvicorn app.main:app`，用种子数据 curl 复现原四个坏现象逐一确认消失：
  /api/fridge、/api/alerts、/api/expire-sweep、/api/consume、改设置后复查、开封流程。
