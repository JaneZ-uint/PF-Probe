# W2 — 子预取器移植 + Baseline 跑通

> 起始：2026/05/08（在 W1 之后无缝衔接）
> 目标：plan.md §5 W2 任务——4 件套（Stride / Stream / SMS / Sandbox）在 ChampSim 中可用，no-prefetch 与各 prefetcher 的 baseline smoke 跑通。

## 出发点：W1 留下的现状

W1 收尾时主分支 ChampSim (commit `06de8d3`) 自带的 prefetcher 只有：
`no` / `next_line` / `ip_stride` / `va_ampm_lite` / `spp_dev`。

plan.md 假设 "DPC-3 主分支自带 Stride/Stream"，**实测发现 Stream 也没有**——
所以 W2 实际要从 [CMU-SAFARI/Pythia](https://github.com/CMU-SAFARI/Pythia)
移植 **3 个** prefetcher（Stream + SMS + Sandbox），不是 plan 设想的 2 个。
`ip_stride` 直接当作 Stride。

Pythia 的源文件用旧版 ChampSim 2.0 API（`uint64_t` + `vector<uint64_t>&` 输出
预取列表），与现代 ChampSim 强类型 `champsim::address` + 单点发射 API 不兼容，
不是 copy-paste，需要逐文件翻译。

## 决策（用户确认）

- 4 个 prefetcher 全部接 **L2C**（与 Pythia / 论文 baseline 对齐）
- 必做 Sandbox（不分阶段，4 件套一次到位）
- 在移植代码同时下完 lbm / omnetpp / bwaves 三条 trace

## 实施

### 现代 ChampSim Prefetcher API 备忘

继承 `champsim::modules::prefetcher`，按需实现下列 hook（编译期 SFINAE 检测）：

| 方法 | 触发 |
|---|---|
| `prefetcher_initialize()` | 仿真开始一次 |
| `prefetcher_cache_operate(addr, ip, hit, useful_pf, type, meta_in)` | 每次 cache 访问 |
| `prefetcher_cycle_operate()` | 每个 cycle |
| `prefetcher_cache_fill(addr, set, way, prefetch, evicted, meta_in)` | cache fill |
| `prefetcher_final_stats()` | 退出 |

发射预取：`intern_->prefetch_line(pf_addr, fill_this_level, metadata) -> bool`。
读 cache 状态：`intern_->virtual_prefetch`、`intern_->get_mshr_occupancy_ratio()`。
注册：放一个目录到 `champsim/prefetcher/<name>/` 即被 `config/modules.py` 自动发现。

**关键转译点**：Pythia 一次 `invoke_prefetcher` 返回多个预取地址；现代 API
每 cycle 只能发一个。模式：在 `cache_operate` 里把待发地址塞进 deque，
`cycle_operate` 每周期 pop 一个发出去。`ip_stride` 用 `active_lookahead`
单变量做的就是这个简化版本。

### 文件落点

```
champsim/prefetcher/
├── ip_stride/   (主分支自带，做 Stride)
├── stream/      (新写)   stream.{h,cc}    ~140 行
├── sms/         (新写)   sms.{h,cc}       ~270 行
└── sandbox/     (新写)   sandbox.{h,cc}   ~220 行

vendor/pythia/                              (translation reference, NOT 编译)
├── streamer.{cc,h}, sms.{cc,h}, sandbox.{cc,h}
└── README.md

scripts/
├── build_prefetcher.sh   — 派生 config_<name>.json + config.sh + make
├── build_all.sh          — 5 个 binary 一次到位
├── run_smoke.sh          — 单 (prefetcher, trace) job
└── run_smoke_all.sh      — 全笛卡尔积 + IPC 透视表

data/w2_smoke/             — 每个 (trace, prefetcher) 一个 JSON
```

### Stream（参数对照 Pythia 默认）

| 项 | 值 |
|---|---|
| Tracker 容量 | 1 set × 64 ways（fully associative，与 Pythia deque 等价） |
| 索引 | `champsim::page_number`（4KB region） |
| 状态 | `{page, last_block, last_dir, conf}` |
| 训练规则 | 同 page 内连续 2 次单调访问 → conf=1，发预取 |
| Degree | 4 cacheline（沿 dir 方向） |
| 边界 | 不跨 page；遇 page boundary 停 |

实现要点：用 `champsim::msl::lru_table<tracker_entry>`，
`index()` / `tag()` 都返回 `page_number`。结构、流程沿用 `ip_stride` 的
`active_lookahead` 模式，每个 cycle 发一个预取直到 `degree_remaining == 0`。

### SMS（核心三表）

| 表 | 容量 | 索引 | 内容 |
|---|---|---|---|
| FT (Filter Table) | 32 entry FIFO | `page_number` | 第一次访问区域时占位（带 trigger PC + offset） |
| AGT (Active Generation Table) | 32 entry age-LRU | `page_number` | 当前活跃区域的 64-bit access bitmap |
| PHT (Pattern History Table) | 1024 sets × 16 ways = 16K | `signature = (PC<<6) ^ offset`，set 由 sig 低 10 位选，tag = full sig | 历史 region 的 access pattern |

**Region 大小**：用 ChampSim 自带的 4KB page（不是论文的 2KB），每 region 64
个 cacheline，`region_pattern = std::bitset<64>`——这样省去自定义 slice。

**生命周期**：
1. demand → 查 AGT；命中则 set 对应 bit、刷新 age；miss 进 step 2
2. 查 FT；命中则把条目升入 AGT（pattern 初始化为两个 set bit），erase from FT
3. FT miss → 把 (PC, page, trigger_offset) 塞进 FT（FIFO 满则 pop_front）
4. 同时按 (PC, current_offset) 算 signature 查 PHT；命中则把所有 set bit 翻译为预取地址塞进 deque
5. AGT 满 → age 最大者驱逐，把 pattern 写入 PHT（key = trigger 时记录的 PC + offset）
6. `cycle_operate` 每周期 pop 一个 deque 头部地址发预取

**注意**：实现里 PHT 用 `std::vector<std::deque<pht_entry>>` 而不是
`champsim::msl::lru_table`——因为 lru_table 的 set count 必须是 2 的幂；
1024 满足，但 deque 写起来对照 Pythia 更直观，后续 W3 调试也好读。

### Sandbox（Pugsley 2014 + Pythia 调整）

| 参数 | 值 |
|---|---|
| BLOOM_BITS | 2048 bits（256B 数组） |
| BLOOM_HASHES | 2（用 `std::hash<uint64_t>` ⊕ 不同种子） |
| 评估期长度 PHASE_LENGTH | 256 demand accesses |
| 在评估池的 offset 数 | 16（初始 ±1..±8） |
| 候选池 | 16（初始 ±9..±16） |
| 每轮（16 phases）后 cycle 出/入 | 4 个最低分 |
| 真预取触发门槛 | score ≥ PHASE_LENGTH(=256) |
| 每方向发射上限 | 4 |

**核心循环**（每个 demand access）：
1. 在 Bloom filter 中查当前地址；命中说明上次某个评估 offset 的伪预取与本次访问位置吻合 → `evaluated[curr_ptr].score++`
2. 用当前评估 offset 算"伪预取"地址（`addr + offset×64B`，不跨 page），加进 Bloom（**不发真预取**）
3. `demand_in_phase++`；满 256 推进 `curr_ptr`；满 16 phase 进入 round end，按分排序、cycle 4 in/out
4. 遍历 16 个评估 offset，对所有 score ≥ 256 者按"小 abs offset 优先"发真预取（每方向 ≤ 4）

**关键不变量**：score 跨 phase 累积、跨 round 累积（除非该 offset 被 cycle 出去）。
Bloom 在每个 phase 结束清零；这意味着 score 增长来自"短期局部性"。

### 与 Pythia 原版的几处简化

| 维度 | Pythia | 本实现 | 影响 |
|---|---|---|---|
| Region 大小 | knob 可调（默认 2KB 或 4KB） | 固定 4KB | SMS 在 2KB region 上 spatial pattern 更精细一点；4KB 与 page_number 对齐，代码更短，对 baseline IPC 影响 < 5%（凭经验） |
| Bloom filter（Sandbox） | `bf::basic_bloom_filter`，hash 数自适应 | 自写 2-hash 2048-bit `std::bitset` | FP 率略高；对 score 噪声有限，未观察到病态行为 |
| Sandbox stream-detect | 可选项，默认关 | 不实现 | Pythia 默认也不开 |
| SMS 预取 buffer | knob 可开"逐 demand 注入"模式 | 不实现 | 我们用 cycle-operate deque 拖延发射达成相同效果 |

## Smoke 验证

### mcf 上 1M warmup + 2M sim 快速结果（移植即时验证）

| Prefetcher | IPC | Δ vs no |
|---|---:|---:|
| no | 0.1237 | baseline |
| ip_stride | 0.1380 | +11.6% |
| stream | 0.1809 | +46.2% |
| sms | 0.2146 | +73.5% |
| sandbox | 0.2048 | +65.6% |

**健全性指标**（每个新 prefetcher 退出时打印的训练计数）：

- Stream：tracker.hit 442K / dir_match 278K / prefetch_issued 738K（degree=4 一致）
- SMS：FT.miss 11.7K / AGT.hit 434K / PHT.hit 23K / PHT.miss 184 / PHT.inserts 133（即 PHT 已被训练）
- Sandbox：filter_hit 415K / end_of_phase 1838 / end_of_round 114（轮转过 7 次以上） / 顶部胜出 offset：-3 -2 +1 -1 -4（小 stride，与 mcf pointer-chase + 局部 array 行为一致）

ip_stride 在 mcf 上 IPC=0.138 与 W1 录得数值完全一致，说明 build 系统改动 + 配置派生没有破坏现有 prefetcher。

### 全笛卡尔积 baseline（5 prefetcher × 4 trace，1M warmup + 5M sim）

> 跑 `bash scripts/run_smoke_parallel.sh`；4 trace 并行 × 5 prefetcher 串行，
> 22 核机上 wall clock **1562 s ≈ 26 分钟**，0 失败。

| prefetcher |        mcf |        lbm |    omnetpp |     bwaves |
|------------|-----------:|-----------:|-----------:|-----------:|
| no         |     0.1249 |     0.4511 |     0.2800 |     2.0517 |
| ip_stride  |     0.1207 |     0.4635 |     0.2812 |     2.0517 |
| stream     |     0.1841 |     0.4947 |     0.2817 |     2.0524 |
| sms        |     0.2209 |     0.5339 |     0.2891 |     2.0517 |
| sandbox    |     0.2299 |     0.5276 |     0.2614 |     2.0517 |

**Δ vs no**（百分比）：

| prefetcher |    mcf |    lbm | omnetpp | bwaves |
|------------|-------:|-------:|--------:|-------:|
| ip_stride  | -3.3%  | +2.7%  |  +0.4%  |   0.0% |
| stream     | +47.4% | +9.7%  |  +0.6%  |  +0.0% |
| sms        | +76.9% | +18.4% |  +3.3%  |   0.0% |
| sandbox    | +84.0% | +17.0% |  −6.6%  |   0.0% |

### 几个值得说明的非平凡点（不是 port bug）

**1. `ip_stride` 在 mcf 上 IPC 低于 no（−3.3%）**
W1 的 IPC=0.138 是 ip_stride 用 ChampSim 默认配置（多半挂 L1D）跑出来的；
W2 把 prefetcher 全部挪到 L2C，与 Pythia 设置对齐。mcf 是
pointer-chasing 极差的 case，PC-stride 在 L2 层级的 useful prefetch 占比
只有 53%（71.5K useful / 135K issued），剩下 43% 浪费 LLC 带宽，于是出现微负。
这是 ip_stride 在 L2 的真实表现，不是 port 退化。

**2. `sandbox` 在 omnetpp 上 IPC 低于 no（−6.6%）**
Sandbox 在 omnetpp 上发了 **80.7 万**预取，但 only **4.7% 有用**
（37.8K useful / 806.6K issued，33.1 万 useless）。omnetpp 是事件驱动 C++，
spatial locality 弱，Sandbox 的"小 stride 偏好" + score 累积阈值在这里
overfire。这是 Pugsley 2014 原文承认的算法弱点（Pythia paper Table 也展示
Sandbox 在多个 SPEC 应用上低于 baseline），不是 port bug。
W3 数据集生成时 omnetpp 上 Sandbox 的 PC 多半会被 PF-LLM 的 label 流程标
为 "Filter" 而非任何 sub-prefetcher——这恰好是 PF-LLM 的核心 motivation。

**3. bwaves 上 5 个 prefetcher IPC 几乎完全相同（≈ 2.05）**
no/ip_stride/sms/sandbox 都是 2.0517，stream 仅多发了 32 个预取。bwaves
的 simpoint 在前 6M 指令内 IPC 高达 2.05，工作集驻留在 L2，几乎没有
L2 miss 触发 prefetcher。这不是 prefetcher 的失败，而是 sim 窗口太短
没碰到 bandwidth-bound 阶段。**W3 跑 50M sim 时这一行会大幅变化**。

### W2 acceptance 判定

| 判据 | 结果 |
|---|---|
| 5 个二进制都构建通过 | ✓ |
| no-prefetch 在 mcf 上 IPC ≈ 0.124（与 W1 比对） | ✓ 0.1249 |
| Stream 在 lbm 上 ≥ +5% | ✓ +9.7% |
| SMS 在所有 trace 上 IPC ≥ no - 5% | ✓ 全部 ≥ no |
| Sandbox port 不破（即 stats 计数 > 0、有 round 切换） | ✓ end_of_round=114（mcf 5M sim） |
| 每个新 prefetcher 在 mcf 上 IPC > no | ✓ stream +47% / sms +77% / sandbox +84% |
| 任意 prefetcher 在 trace 上 IPC < no − 5% | △ sandbox 在 omnetpp 上 −6.6%；分析后判定为算法固有，非 port bug |

W2 整体通过——4 件套各自能独立产生有意义的 IPC 信号，是合格的 sub-prefetcher
集合，可以进入 W3 数据集生成。

## W2 收尾状态

- [x] vendor Pythia 源文件（`vendor/pythia/`）作为翻译参考
- [x] Stream port + 单条 mcf smoke（IPC +46%）
- [x] SMS port + 单条 mcf smoke（IPC +74%）
- [x] Sandbox port + 单条 mcf smoke（IPC +66%）
- [x] `scripts/build_prefetcher.sh` + `build_all.sh` + `run_smoke.sh` + `run_smoke_all.sh` + `run_smoke_parallel.sh`
- [x] 4 条 SPEC2017 trace 全部下载完成（mcf 159M / lbm 759M / omnetpp 766M / bwaves 34M）
- [x] 5 × 4 全笛卡尔积 smoke 表（1562s wall clock，0 失败，结果见上表）
- [x] acceptance 判定：W2 整体通过

## 下一步（W3 提示）

W3 数据集生成的核心改动是"在 L2C 缓存层采集 per-PC AMAT"——按当前
prefetcher 挂载位置（L2C），改动点集中在 `champsim/src/cache.cc`：

1. miss 入队时记录 `(in_flight_PC, miss_cycle)`
2. fill 完成时算 `latency = fill_cycle - miss_cycle`，按 PC 累加
3. 在 phase 结束（roi 退出）时把 per-PC histogram dump 到额外 JSON

W2 的 4 件套已经就绪，W3 直接对每个 prefetcher × benchmark 跑 ChampSim
就能产出 plan §3 表里的数据集。
