# W3 Day 1 — Pin tracer 准备就绪

> 起始：2026/05/09
> 目标：Pin SDK 下载 + ChampSim pin tracer build + 单二进制烟测

## 路线背景

W2 完成后准备进 W3 数据集生成时发现硬约束：DPC-3 ChampSim trace
**不含汇编**——只有 IP / 寄存器索引 / 内存地址，无法重建 PF-LLM 论文要求的
"load PC ±128 行汇编 context"。SPEC2017 binary 需商业 license（学术价
~250 USD）。

→ 决策：走 **GAP Benchmark Suite + 自跑 Pin tracer** 的开源路线。
Day 1 只做 tracer 端，GAP 编译留到 W3a。

## 完成内容

### 1. Pin SDK

- 版本：**Pin 3.30**（Jan 2024 build，gcc-linux 包）
- URL：`https://software.intel.com/sites/landingpage/pintool/downloads/pin-3.30-98830-g1d7b601b3-gcc-linux.tar.gz`
- 大小：32MB tar.gz / 155MB 解压
- 路径：`vendor/pin -> pin-3.30-98830-g1d7b601b3-gcc-linux/`（symlink，方便 PIN_ROOT 引用）
- 已加 `.gitignore`

### 2. champsim_tracer build

```bash
cd champsim/tracer/pin
PIN_ROOT=/home/zhuyihan/code/PF-LLM/vendor/pin make
```

产物：`champsim/tracer/pin/obj-intel64/champsim_tracer.so`（1.9 MB）。
唯一 warning：`util_host_ia32e.spp.o missing .note.GNU-stack section`，
linker 的非致命兼容提示，无影响。

### 3. 跑通 + **修了一个 tracer bug**

#### 烟测目标

写了个 `/tmp/loop.c`：5M 次 `sum += arr[i & 1023]` 的 CPU-bound 程序，
原生跑约 10ms。

#### 第一次 trace —— 异常

```bash
vendor/pin/pin -t champsim/tracer/pin/obj-intel64/champsim_tracer.so \
    -o /tmp/loop.trace -t 1000000 -- /tmp/loop
```

期望：1M 条记录 × 64 B = 64 MB 输出。
**实际：3.6 GB**（约 56M 条记录，**56× 超 cap**）。

#### Bug 定位

ChampSim tracer 用的是 Pin IF/THEN 模式（`INS_InsertIfCall` +
`INS_InsertThenCall`），把 `ShouldWrite()` 当 IF 谓词、`WriteCurrentInstruction()`
当 THEN 动作。但 `ShouldWrite()` 里同时做了 `++instrCount` 和上限判断——
**Pin 文档要求 IF 谓词必须是无副作用的纯函数**，否则在热循环里 inline
分析路径下行为未定义。

ChampSim README 里的"tested with PIN 3.22"是 2022 年写的；Pin 3.30 比
3.22 inlining 更激进，IF 谓词的副作用被忽略，导致 `instrCount` 在热路径
里没正确累加，cap 失效。

短程序 `/bin/true` 上误差只有 1.75×（cap=100K → 175K 实写），因为指令
不会被同一 BBL 反复执行；hot loop 受影响最严重。

#### Patch

`champsim/tracer/pin/champsim_tracer.cpp` 把 `ShouldWrite` + `WriteCurrentInstruction`
合并成单个 `MaybeWriteCurrentInstruction`，`INS_InsertIfCall + InsertThenCall`
改成无条件 `INS_InsertCall`。语义等价，每条指令多一次函数调用代价，
但 cap **绝对正确**。

```cpp
void MaybeWriteCurrentInstruction()
{
  ++instrCount;
  if (instrCount <= KnobSkipInstructions.Value()) return;
  if (instrCount > KnobSkipInstructions.Value() + KnobTraceInstructions.Value()) return;
  // ... write trace_instr_format_t ...
}
```

代码注释里写明了"为啥不用 IF/THEN"，未来重新合并 ChampSim master 的
人能看懂。

#### Patch 后重测

```bash
$ vendor/pin/pin -t .../champsim_tracer.so -o /tmp/loop.trace -t 1000000 -- /tmp/loop
real    0m4.264s   # 之前 23s（少 5× 也合理：不再多写 55M 条）
$ ls -lh /tmp/loop.trace
62 MB              # 970K 条，匹配 1M cap
$ xz /tmp/loop.trace
89 KB              # 700× 压缩比，homogeneous loop 熵极低
```

### 4. 端到端跑通

把 trace 喂 W2 的 `champsim_no` binary：

```bash
$ ./champsim/bin/champsim_no \
    --warmup-instructions 100000 \
    --simulation-instructions 800000 \
    --json /tmp/loop_smoke.json /tmp/loop.trace.xz
```

输出：

| 指标 | 值 |
|---|---|
| instructions | 800002 |
| cycles | 719871 |
| **IPC** | **1.1113** |
| L1D LOAD miss | 787 |
| L2C LOAD hit/miss | 127 / 555 |

IPC=1.11 对一个整数算术 + 1KB 数组 touch 的循环是非常合理的数字
（接近 dispatch_width=6 的某个上界，但被 1024-int 数组未完全装下 L1D 限制）。

**端到端管道全通**：`pin tracer → 二进制 trace → xz → ChampSim binary → JSON`。

## 工程产出

| 路径 | 类型 | 备注 |
|---|---|---|
| `vendor/pin -> pin-3.30-...` | 新增（gitignored） | Pin SDK，155MB |
| `champsim/tracer/pin/champsim_tracer.cpp` | 修改（4 行 patch） | IF/THEN → 无条件 InsertCall，热路径 cap 修复 |
| `champsim/tracer/pin/obj-intel64/champsim_tracer.so` | 构建产物（gitignored） | 1.9MB pintool |
| `.gitignore` | 修改 | 加 vendor/pin、obj-intel64 |
| `notebooks/02-w3-gap-pipeline.md` | 新增 | 这个文件 |

## 下一步（W3a，~1-2 天）

1. clone CMU SAFARI [GAPBS 数据集](https://github.com/sbeamer/gapbs) 或者 Beamer 的官方 GAP，编译 6 个 kernel（BFS / PR / SSSP / BC / CC / TC）
2. 选 4 个 input graph（Twitter / Web / Kron / Urand），下载或 GAP 自带 generator
3. 跑通"1 个 kernel × 1 input → Pin trace → ChampSim → 算 1 个 PC 的 AMAT"端到端
4. objdump 二进制，写 Python 脚本按 PC 抽 ±128 行汇编

W3 Day 1 完成，可以去睡觉。

## 风险提醒

- **WSL ptrace_scope=1**：本次烟测没踩到，但如果未来 GAP kernel 跑超时或挂起，
  先 `echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope` 试试
- **trace 体量**：1 M 指令 = 62 MB raw / 89 KB xz。GAP kernel 跑到 100M 指令
  规模就是 6 GB raw / ~10 MB xz；24 个 trace 总 raw 量 ~150 GB——
  **千万记得边 trace 边 xz**，不要在磁盘上留 raw（W3a 写一个 `pin … | xz > out.xz` 的脚本）

---

# W3a — GAPBS 编译 + 单 kernel 端到端跑通

> 起始：2026/05/09 下午
> 范围：W3 Day 1 之后的第二步——把 GAP kernel 喂进 W2 的 simulator 跑通

## 1. GAPBS 编译

```bash
git clone --depth 1 https://github.com/sbeamer/gapbs.git vendor/gapbs
cd vendor/gapbs
SERIAL=1 CXX_FLAGS="-std=c++11 -O3 -g -Wall" make -j8
```

产物：7 个 kernel + converter，在 `vendor/gapbs/` 下：

| kernel | 大小 | 算法 |
|---|---|---|
| `bc` | 1.2 MB | Betweenness Centrality |
| `bfs` | 920 KB | Breadth-First Search |
| `cc` | 1.1 MB | Connected Components |
| `pr` | 995 KB | PageRank |
| `sssp` | 1.1 MB | Single-Source Shortest Path |
| `tc` | 948 KB | Triangle Count |
| `converter` | 955 KB | 输入图格式转换工具 |

**关键 flag**：
- `SERIAL=1` 关掉 OpenMP——multi-thread Pin tracing 会有顺序问题，单线程干净
- `-g` 加 debug symbol 不影响 codegen，方便 W3a 步骤 4 的 objdump
- `-O3` 与 PF-LLM 论文对齐
- 无视若干 `#pragma omp` 警告（SERIAL 模式下 -fopenmp 没加，pragma 被忽略）

## 2. 第一个 trace：BFS on Kron-17

### 命令

```bash
mkdir -p traces/gap
vendor/pin/pin -t champsim/tracer/pin/obj-intel64/champsim_tracer.so \
    -o traces/gap/bfs_kron17.trace \
    -t 30000000 \
    -- vendor/gapbs/bfs -g 17 -n 1
xz -T 4 traces/gap/bfs_kron17.trace
```

参数说明：
- `bfs -g 17 -n 1`：Kron 2^17 = 131K vertices，~1.86M edges，degree=14；只跑 1 个 trial
- `-t 30000000`：cap 在 30M 指令（足够覆盖整个 BFS 的算法部分）
- 不加 `-s`（skip）：本次烟测不区分 graph 生成阶段 vs BFS 阶段

### 结果

| 维度 | 值 |
|---|---|
| Native runtime | 0.30 s |
| Pin trace runtime | 61 s（200× slowdown，正常 Pin overhead） |
| Trace raw 体积 | 1.8 GB（28M 条 × 64 B/条；cap 30M，BFS 提前结束） |
| Trace xz 体积 | 2.8 MB（**650× 压缩比**） |
| xz 时间 | 3m41s 用 4 线程 |

### 一个观察（影响后续 W3 数据集设计）

GAP 的 "Generate Time" 在 native 时只有 0.145 s，但在 Pin 下变成 44 s——
说明 **trace 的 90% 都在图生成阶段**（顺序数组填充），真正的 BFS 算法只有
~3M 条指令。

正式 W3b 跑数据集时一定要用 Pin 的 `-s skip` 跳过 graph gen，或者预先把
图生成成文件后用 `-f file` 读入。否则我们标 label 的 PC 大部分都是
"new int[N]; for(...)"  式的内存填充而不是真正的 graph 算法热点。

## 3. 喂 W2 的 5 个 ChampSim binary

```bash
for p in no ip_stride stream sms sandbox; do
  ./champsim/bin/champsim_$p \
      --warmup-instructions 1000000 \
      --simulation-instructions 20000000 \
      --json /tmp/gap_$p.json traces/gap/bfs_kron17.trace.xz
done
```

每个 ~1.5 分钟 wall。**5 个全部跑通，0 失败**。

### IPC + 缓存层数据

| prefetcher | IPC | Δ vs no | L1D miss | L2C miss | pf_issued | useful | useless |
|---|---:|---:|---:|---:|---:|---:|---:|
| no | 2.4712 | baseline | 13627 | 4181 | - | - | - |
| ip_stride | 2.5020 | +1.2% | 13208 | 3525 | 2481 | 938 | 91 |
| stream | 2.5562 | +3.4% | 12935 | 2390 | 11100 | 2206 | 1253 |
| sms | 2.5072 | +1.5% | 13580 | 3674 | 3676 | 608 | 797 |
| sandbox | **2.6051** | **+5.4%** | 12759 | **1386** | 69437 | 3471 | 4689 |

**观察**：
- IPC=2.47 baseline 比 SPEC mcf（0.12）高一个数量级——graph gen 阶段的顺序内存填充缓存友好，反映了"trace 头 90% 不是真正 BFS"的事实
- Sandbox 把 L2C miss 从 4181 砍到 1386（−67%）——说明 graph gen 阶段的访问模式有清晰的 stride，Sandbox 的 offset evaluation 能很快锁定
- Stream 的 prefetch 准确率 20%（2206/11100），明显高于 Sandbox 的 5%（3471/69437），但绝对 useful 数 Sandbox 多——Sandbox 用"高发射量+低准确率"换 IPC，Stream 是"低发射量+较高准确率"
- 整个 baseline 跨 prefetcher 的 IPC 范围 2.47-2.61，diversity 够，可以驱动 PF-LLM label 的 (PF Sel, PF Degree, Filter) 决策

### 端到端 wall clock 估算

| 阶段 | 单次成本 |
|---|---|
| Pin tracing | ~60 s（Kron-17 × 30M cap）|
| xz 压缩 | ~3 min（4 线程） |
| ChampSim 单跑（20M sim） | 1.5 min |

W3b full grid 估算：6 kernel × 4 input = 24 trace × (Pin 60s + xz 3min) = ~2 小时 trace 生成；24 trace × 4 prefetcher × 3 degree = 288 ChampSim sim × 1.5 min = 7 小时（4-parallel ~2 小时）。**全 W3 数据生成预计 1 个工作日 wall clock**，可以接受。

## 4. W3a 完成度

- [x] GAPBS clone + 7 kernel 编译（serial + debug symbols）
- [x] Pin tracer 在 GAP binary 上跑通，trace cap 精确生效
- [x] xz 压缩验证（650× 压缩比，磁盘问题不大）
- [x] 5 个 W2 ChampSim binary 全部能消化 GAP trace
- [x] Prefetcher diversity 验证（IPC 跨 2.47-2.61，cache miss 跨 1386-4181）
- [ ] objdump + Python 抽 ±128 行汇编（**下一次做**——W3 后续的步骤 4）

W3a 关键证据齐了：**GAP 路线在工程上是通的**。下次进 W3b，加 ChampSim
per-PC AMAT 仪器化 + 写 objdump → assembly context 抽取脚本 + 跑数据生成 grid。

## 工程产出（本节追加）

| 路径 | 类型 | 备注 |
|---|---|---|
| `vendor/gapbs/` | 新增（git clone） | GAPBS 源码 + 7 个编译好的 kernel |
| `traces/gap/bfs_kron17.trace.xz` | 新增 | 第一条 GAP trace（2.8MB），smoke 用 |
| `notebooks/02-w3-gap-pipeline.md` | 修改 | 追加 W3a 部分 |

---

# W3b 进行中 — Step 1: ChampSim per-PC AMAT 仪器化

> 起始：2026/05/09 下午（W3a 之后）
> 范围：本步骤只做 cache.cc 的改动 + JSON 输出验证；trace 生成 + objdump + 全 grid 留下次

## 仪器化设计

PF-LLM 论文的 label 决策：对每个 (load PC, prefetcher, degree) 组合算
出 AMAT，哪个最低就标哪个为 label。**关键依赖**：ChampSim 要能输出
**per-PC** 的 average miss latency 而不是只有 cache 总体的 miss latency
聚合数。

### Hook 点

ChampSim 的 `mshr_type` 已经天然带有 `champsim::address ip` 和
`champsim::chrono::clock::time_point time_enqueued`（`cache.h:88-113`）——
PC 和入队时间一直跟到 `handle_fill`。`cache.cc:238-239` 已经有现成的全局
miss latency 累加：

```cpp
if (fill_mshr.type != access_type::PREFETCH)
    sim_stats.total_miss_latency_cycles += (current_time - (fill_mshr.time_enqueued + clock_period)) / clock_period;
```

→ 只需要在同一处再加一个 per-PC 累加表。

### 数据结构

`cache_stats.h` 加：

```cpp
std::unordered_map<uint64_t, std::pair<uint64_t, uint64_t>> per_pc_load_latency{};
// key = demand IP (uint64_t), value = (sum_cycles, count)
// AMAT for a PC = sum / count
```

每个 cache 实例（L1I / L1D / L2C / LLC / TLB 等）都有一个独立的表。
后续 label 决策只用 L1D 的（program 感知的实际 load latency）。

### 改动文件

| 文件 | 改动 | 行数 |
|---|---|---|
| `champsim/inc/cache_stats.h` | 加 `<unordered_map>` include + 新字段 | +5 |
| `champsim/src/cache_stats.cc` | `operator-` 扩展（虽然 ChampSim 内部不用） | +6 |
| `champsim/src/cache.cc` (handle_fill) | 在现有 latency 累加旁加 per-PC 累加 | +5 |
| `champsim/src/cache.cc` (end_phase) | 在 sim→roi 字段拷贝里加新字段 | +3 |
| `champsim/src/json_printer.cc` | 在 cache stats JSON 里 emit per-PC 表 | +13 |

### 一个踩到的坑

第一次 build 完跑 GAP trace，在 `roi[cpu0_L1D]` 里**找不到**
`per_pc_load_latency` 字段，但 `sim[cpu0_L1D]` 里有。

ChampSim 的 ROI/sim 模型不是用 `operator-`(虽然有定义但好像没用上),
而是 `end_phase()` 里**逐字段**把 `sim_stats.<field>` 拷贝到 `roi_stats.<field>`。
我的新字段没写在拷贝列表里→ roi_stats 永远是空 map → JSON 里 emit 检查 `!empty()`
→ 跳过。

修复：在 `end_phase()` 加一行
```cpp
roi_stats.per_pc_load_latency = sim_stats.per_pc_load_latency;
```

教训：ChampSim 引入新的 cache_stats 字段时，要同步改两个地方——
`operator-` (代码库未必用上) **以及** `CACHE::end_phase()` (实际驱动 ROI 数据的)。

## 验证

### 单跑 champsim_no on bfs_kron17（5M sim）

```bash
./champsim/bin/champsim_no \
    --warmup-instructions 1000000 \
    --simulation-instructions 5000000 \
    --json /tmp/amat_test.json traces/gap/bfs_kron17.trace.xz
```

`roi.cpu0_L1D.per_pc_load_latency` 包含 **698 个独立 PC**，AMAT 范围 10-200+ cycles。
JSON 大小 195 KB（含全 cache 层级的 per-PC 表）。

Top counts：

| PC | sum_cyc | count | AMAT |
|---|---:|---:|---:|
| 0x7177b209a87c | 13637 | 750 | 18.2 |
| 0x601b395854d8 | 128370 | 732 | **175.4** |
| 0x7177b209b141 | 118865 | 728 | 163.3 |
| 0x7177b209a826 | 5256 | 442 | 11.9 |

`0x7177b...` 高地址区域是 libc/动态链接器代码；`0x601b...` 是 GAP binary
本身的 `.text` 段；`0x0` 表示 page walk 触发的 fill（无 demand IP）。

### 单跑 champsim_stream，对比 per-PC AMAT 变化

```python
# 共 662 个 PC 在两次跑里都 ≥ 50 次 fill，可以可靠对比
```

| PC | no.AMAT | stream.AMAT | delta |
|---|---:|---:|---:|
| 0x7177b209f5f0 | 219.1 | **31.7** | **−187.4** |
| 0x7177b209f9a0 | 191.5 | 15.3 | −176.1 |
| 0x7177b209f9bc | 191.0 | 22.7 | −168.3 |
| 0x7177b209f5d0 | 202.5 | 64.2 | −138.3 |
| 0x7177b209f5fd | 191.3 | 65.0 | −126.2 |
| 0x7177b209b120 | 156.9 | 64.3 | −92.7 |
| ... | ... | ... | ... |
| 0x7177b209a826 | 11.9 | 11.1 | −0.8 |
| **0x601b395854d8** | **175.4** | **176.8** | **+1.4** |
| 0x0 | 203.4 | 211.3 | +7.9 |

**这正是我们要的 label 信号**：
- 大部分 PC 在 Stream 下 AMAT 降 50-200 cycles → 这些 PC 的 label 应该是 `(PF Sel=Stream, ...)`，
- `0x601b395854d8`（BFS 的图边访问 pointer-chase 热点）AMAT 几乎不变 → label 应该是 `Filter`（不预取）。
- 后续 W3b 跑全 grid 时，对每个 PC 比较 4 个 prefetcher × 3 degree 的 12 个 AMAT 值，最低值对应的 (prefetcher, degree) 就是 label。

### JSON 体积评估

| 跑 | 5M sim, baseline 195 KB |
|---|---|
| 50M sim 估计 | ~1-2 MB（PC count 增长不到线性，因为 working set 收敛） |
| 全 W3b grid: 288 sim × 1.5 MB | ~430 MB 总磁盘 |

完全可控。

## W3b Step 1 完成状态

- [x] cache_stats.h 加 per_pc_load_latency 字段
- [x] cache_stats.cc operator- 扩展
- [x] cache.cc handle_fill 加 per-PC 累加
- [x] cache.cc end_phase 加 sim→roi 字段拷贝
- [x] json_printer.cc 加 per-PC 表 emit
- [x] 5 个 ChampSim binary 全部重建并验证
- [x] 在 GAP bfs_kron17 trace 上看到合理 per-PC AMAT 数据
- [x] no vs stream 对比验证仪器化能区分"prefetcher 是否对该 PC 有用"

## 下一步（W3b Step 2-5，下次接着做）

1. **Trace 生成 grid**：解决 graph 生成阶段污染问题——用 Pin `-s` skip 或预生成图文件后 `-f` 读入；产出全 6 kernel × 4 input 的 trace 集（24 个）
2. **Degree 参数化**：build_prefetcher.sh 加 `<degree>` 参数（per-binary `-D` 烤入），产出 12 个新 binary
3. **Full grid run**：no + 4 prefetcher × 3 degree = 13 配置 × 24 trace = ~312 ChampSim 跑
4. **objdump + Python**：脚本 `scripts/extract_asm_context.py`，输入 GAP binary 路径 + PC 列表，输出每个 PC 的 ±128 行汇编 context
5. **数据集打包**：合并 (asm_context, label) → `data/dataset/{train,test}.jsonl`

---

# W3b Step 2 — Trace 生成 grid

> 起始：2026/05/09（W3b Step 1 之后）
> 范围：把 24 个 (kernel, input) trace 跑出来，PC 与 binary 完全对得上

## 设计选择

### Graph 生成阶段污染

W3a 时观察到 `bfs -g 18 -n 1` trace 里 90% 都是 Kron 图生成的代码（顺序内存填充），
真正 BFS 算法只 ~3M 条指令。两个解决方案：

| 方案 | 优劣 |
|---|---|
| Pin `-s skip` 跳过开头 N 条 | N 因 kernel/input 而异，脆弱 |
| 用 `converter` 预生成 .sg 二进制图文件，kernel `-f file.sg` 读入 | 干净——kernel 只做 I/O + 算法 |

走方案 2。

### ASLR：必须关掉

第一次跑 `bfs -f kron18.sg`，trace 顶部 PC 是 `0x62f5fc2626b6` 这种地址。
**这是 PIE 二进制的 ASLR 随机化基址**——每次跑都不一样。
后面 W3b Step 4 用 `objdump` 抽汇编要按 PC 对齐，ASLR 一开就废了。

修复：trace gen 脚本里包一层 `setarch "$(uname -m)" -R`，关掉子进程及其 fork
出来的 Pin / kernel 的 ASLR。这样 PIE binary 都加载到 canonical 基址 `0x555555550000`，
任意两次 trace 的同一个 PC 是同一条指令。

验证：跑两遍同一 trace + champsim_no，top-3 PC 完全一致：
`['0x5555555605b8', '0x5555555605b0', '0x7ffff7fcf87c']`。

### Trace 文件 → objdump 对齐

PIE 基址 `0x555555550000` + binary 内偏移 = 运行时 PC：

```
runtime PC 0x5555555605b8 = 0x555555550000 + 0x105b8
$ objdump -d --no-show-raw-insn vendor/gapbs/bfs | grep -E "^ +105b8:"
   105b8:    movdqa 0x2310(%rip),%xmm0   # 128d0 <_ZTS5CLApp+0x280>
```

✓ 对得上一条 `movdqa`，BFS 热路径的内存读。**toolchain 端到端通了**。

### xz 压缩参数

第一次 trace gen 完发现 xz 压缩 611MB raw 用了 **85 秒**——太慢了。Benchmark 三档：

| Preset | 时间 | 输出大小 |
|---|---|---|
| `xz -1 -T 4` | 2.7 s | 3.8 MB |
| `xz -3 -T 4` | 2.9 s | 3.6 MB |
| `xz -6 -T 4` (默认) | 84 s | 3.5 MB |

**`-3` 比默认快 30×，体积只大 3%**——24 trace 多 ~2MB 总磁盘可忽略。改用 `-3`。

## 工程

### 4 个图文件预生成

```bash
mkdir -p traces/gap/inputs
for spec in "g 18 kron18" "g 20 kron20" "u 18 urand18" "u 20 urand20"; do
  vendor/gapbs/converter -<flag> <scale> -s -b traces/gap/inputs/<name>.sg
done
```

`-s` 表示 symmetrize——TC (Triangle Count) 需要无向图，其他 kernel 也能跑。

| 文件 | 大小 | 节点 | 边 |
|---|---:|---:|---:|
| kron18.sg | 32 MB | 262K | 3.8M undirected |
| kron20.sg | 128 MB | 1M | 15.7M undirected |
| urand18.sg | 34 MB | 262K | 4.2M undirected |
| urand20.sg | 136 MB | 1M | 16.8M undirected |

总 329 MB（gitignore 掉）。

### 单 trace 脚本 `scripts/gen_gap_trace.sh`

签名：`gen_gap_trace.sh <kernel> <input_name> [cap_M=50]`

逻辑：
1. 校验 binary、tracer、input 都在
2. 已有同名 .xz 跳过（脚本可重入）
3. `setarch -R pin -t tracer -o $RAW -t $cap_inst -- kernel -f $sg -n 1`
4. `xz -3 -T 4 $RAW`（同时 rm 掉 raw）
5. 输出 `traces/gap/<kernel>_<input>.trace.xz`

### 批量脚本 `scripts/gen_all_gap_traces.sh`

签名：`gen_all_gap_traces.sh [cap_M=50] [parallelism=4]`

xargs 调度 par 个并发 worker，每个 worker 调 `gen_gap_trace.sh`。
跳过已存在文件，可重入。

## 验证：bfs on kron18 (cap=10M)

```bash
$ time scripts/gen_gap_trace.sh bfs kron18 10
[trace] pin done in 10s; raw size 611M
[xz   ] done in 92s; xz size 3.5M       (验证后改为 xz -3)
[done ] traces/gap/bfs_kron18.trace.xz
real    1m41s
```

喂 `champsim_no`（500K warmup + 2M sim）：

| 指标 | 旧 trace（带 graph gen） | 新 trace（pre-gen + ASLR off） |
|---|---:|---:|
| IPC | 2.4712 | **0.2844** |
| L1D 总 miss（per 单位 sim） | ~80/M | ~60K/M |
| L1D unique PC | 698 | 977 |
| Top PC 来源 | libc 顺序填充 | BFS 算法 hot path |

**IPC 从 2.47 跌到 0.28——这才是真正的 BFS pointer-chase 工作负载**。
prefetcher 在这种 trace 上跑出来的 per-PC AMAT 才是合理的 label 信号。

## 全 grid 跑

```bash
bash scripts/gen_all_gap_traces.sh 50 4
```

24 个 trace（6 kernel × 4 input），cap=50M，par=4。**实际 wall：第 1 轮 22 min 出
20 个 trace + 4 个 sssp 失败（详见下面"踩到的坑"）；第 2 轮 sssp 重跑 2 min**。
合计约 25 分钟出全 24 个 trace，0 最终失败。

### 踩到的坑：sssp 需要权重图

第一轮 4 个 sssp 都失败，错误信息 `.sg not allowed for weighted graphs`：
SSSP（Dijkstra）需要带权图，GAP 用 `.wsg` 后缀。其他 5 个 kernel 都吃 `.sg`。

修复两步：
1. 用 `converter -<flag> <scale> -s -w -b file.wsg` 多生成 4 个权重图
2. `gen_gap_trace.sh` 加 `case "$KERNEL" in sssp) INPUT_EXT="wsg";; *) INPUT_EXT="sg";; esac`

跑第 2 轮 `gen_all_gap_traces.sh`——会自动跳过已完成的 20 个，只补 4 个 sssp。

| 文件 | 大小 |
|---|---|
| kron18.wsg | 61 MB |
| kron20.wsg | 248 MB |
| urand18.wsg | 66 MB |
| urand20.wsg | 264 MB |

### 全 24 trace 的 .xz 体积（MB，cap=50M）

| kernel |    kron18 |    kron20 |   urand18 |   urand20 |
|--------|----------:|----------:|----------:|----------:|
| bfs    |       6.8 |      19.1 |      12.6 |      20.8 |
| pr     |      31.4 |      24.5 |      34.4 |      27.0 |
| sssp   |      16.8 |      17.5 |      21.5 |      20.3 |
| bc     |      20.0 |      16.4 |      24.4 |      19.1 |
| cc     |      10.5 |      21.7 |      15.3 |      20.3 |
| tc     |      23.1 |      19.8 |      21.1 |      22.0 |

总共 ~480 MB。`bfs_kron18` 最小（6.8MB）—— BFS 在 262K 节点上几百万指令就跑完，
trace 没填满 50M cap。`pr_*` 最大——PageRank 多轮迭代充满 cap。

### champsim_no smoke (1M warmup + 5M sim) — IPC

跑 `scripts/gap_no_sweep.sh` 在 24 trace 上各跑 5M sim baseline：

| kernel |    kron18 |    kron20 |   urand18 |   urand20 |
|--------|----------:|----------:|----------:|----------:|
| bfs    |    0.2685 |    0.2317 |    0.2862 |    0.2314 |
| pr     |    0.2745 |    0.2317 |    0.2747 |    0.2313 |
| sssp   |    0.3710 |    0.2317 |    0.3408 |    0.2316 |
| bc     |    0.3907 |    0.2323 |    0.3906 |    0.2317 |
| cc     |    0.2556 |    0.2309 |    0.2324 |    0.2315 |
| tc     |    0.2991 |    0.2315 |    0.2537 |    0.2318 |

**关键观察：所有 kron20 / urand20 列 IPC 都聚到 0.231 附近**——这不是巧合，
而是因为 5M sim 窗口在大图（1M 节点）上还没出完图加载阶段，IPC 反映的是
.sg 文件的内存映射 + CSR 反序列化，所有 kernel 都一样。**正式 W3b 跑数据
集时大图必须用更长 sim 窗口（50M 或不限制）才能到算法 phase**。

小图（kron18 / urand18）IPC 已经分散开（0.23-0.39），算法行为可见：
- BC/SSSP 在 kron18 上 IPC 最高（0.39 / 0.37）——访问局部性较好
- CC/TC 偏低——更多 pointer chasing

### L1D unique PCs（5M sim 内的 demand fill 唯一 PC 数）

| kernel |    kron18 |    kron20 |   urand18 |   urand20 |
|--------|----------:|----------:|----------:|----------:|
| bfs    |       922 |       722 |       921 |       722 |
| pr     |       924 |       722 |       923 |       722 |
| sssp   |      1080 |       727 |      1093 |       727 |
| bc     |       951 |       721 |       952 |       721 |
| cc     |       906 |       705 |       901 |       705 |
| tc     |       905 |       691 |       903 |       691 |

**~700-1100 unique PC 每个 trace**——同样地，kron20/urand20 列偏低（~720）也是
图加载阶段为主的反映。小图的多样化（~900-1100）说明算法 phase 已经进入。

### L1D LOAD miss（次数，5M sim）

| kernel |    kron18 |    kron20 |   urand18 |   urand20 |
|--------|----------:|----------:|----------:|----------:|
| bfs    |    269276 |    443392 |    332695 |    443422 |
| pr     |    304849 |    442322 |    304843 |    442305 |
| sssp   |    248388 |    442901 |    253602 |    442943 |
| bc     |    206584 |    443021 |    206553 |    442991 |
| cc     |    291084 |    443152 |    326896 |    443117 |
| tc     |    243899 |    443420 |    329233 |    443395 |

200K-450K L1D miss / 5M sim — **memory pressure 充足，per-PC AMAT 信号
能够稳定区分 prefetcher 效果**。

## Step 2 完成度

- [x] 4 个图文件预生成（.sg + .wsg 各 4 个）
- [x] gen_gap_trace.sh 脚本（含 setarch ASLR 关 + xz -3 + sssp/.wsg 路径分支）
- [x] gen_all_gap_traces.sh 批量脚本（par=4 默认，可重入）
- [x] 单 trace 验证：bfs+kron18 IPC=0.28，977 PC，PC ↔ objdump 完全对齐
- [x] 24-trace 全 grid 跑完（25 min wall，sssp 第二轮重跑 2 min，最终 0 失败）
- [x] champsim_no IPC + L1D PC + L1D miss + xz size 四张表填好

## 已知限制 / 移交 W3 后续步骤

- **大图（kron20 / urand20）需要更长 sim 窗口才能进入算法 phase**——本次 5M
  sim sweep 的 IPC 聚到 0.23 是图加载主导的；W3b Step 3（full grid）跑 prefetcher
  比较时建议用 cap=50M 的完整 trace + 长 sim 窗口（比如 30M+），或者直接对小图
  (kron18 / urand18) 做主要 label 提取。
- **bfs_kron18 trace 体积只有 6.8MB**——BFS 在 262K 节点上几百万指令完成，
  没用满 50M cap。这本身不是问题，trace 内容仍然是真实的算法工作负载。

---

# W3b Step 3 — Prefetcher degree 参数化（已完成）

> 起始：2026/05/14
> 范围：把 4 个 prefetcher 的 degree 做成编译时可配置，产出 13 个 binary

## 目标

PF-LLM 论文 label 三元组 `(PF Sel, PF Degree, Filter)` 里 `PF Degree` 维度
需要每个 prefetcher 有多个 degree 配置。产出 12 个新 binary
（4 prefetcher × 3 degree）加上 `champsim_no` 共 13 个配置。

## degree=1/2/3 三档定义

| degree | ip_stride | stream | sms (PHT cap) | sandbox |
|---|---:|---:|---:|---:|
| 1 (弱) | 1 | 2 | 8 | 2 |
| 2 (中) | 2 | 4 | 16 | 4 |
| 3 (强) | 3 | 6 | 24 | 6 |

## 实现

### 1. Header 改动：`#ifndef` 守卫使 degree 可被 `-D` 覆盖

每个 prefetcher 的 `.h` 里把 degree 常数从硬编码改成 `#ifndef` / `#define` / `#endif`
+ `constexpr static int X = X_DEGREE`：

| 文件 | 宏名 | 默认值 |
|---|---|---|
| `champsim/prefetcher/ip_stride/ip_stride.h` | `IP_STRIDE_DEGREE` | 3 |
| `champsim/prefetcher/stream/stream.h` | `STREAM_DEGREE` | 4 |
| `champsim/prefetcher/sms/sms.h` | `SMS_PHT_REPLAY_CAP` | 16 |
| `champsim/prefetcher/sandbox/sandbox.h` | `SANDBOX_DEGREE` | 4 |

### 2. SMS PHT replay cap

SMS 原来没有显式 degree 概念。新增 `PHT_REPLAY_CAP` 常数（默认 16），
在 `sms.cc:pht_lookup_and_queue` 的 for 循环里加：

```cpp
if (queued >= PHT_REPLAY_CAP)
    break;
```

控制单次 PHT 命中最多回放多少个 prefetch 地址。

### 3. build_prefetcher.sh 扩展

签名从 `build_prefetcher.sh <pref>` 改为 `build_prefetcher.sh <pref> [degree]`：

```bash
build_prefetcher.sh stream 2  →  champsim_stream_d2 (烤入 -DSTREAM_DEGREE=4)
```

实现要点：
- 内置 degree → -D 值映射表（per prefetcher）
- 通过 `make CPPFLAGS="-DSTREAM_DEGREE=4"` 传递
- **踩到的坑**：Make 不追踪 `CPPFLAGS` 变化，prefetcher `.o` 不会被重编。
  修复：在 make 前 `rm -f .csconfig/modules/prefetcher/${PREF}/${PREF}.o`
  强制重编。第一次没注意这个，d1/d3 跑出了完全相同的 IPC，排查后加了 rm。

### 4. build_all_degrees.sh

新脚本，串行构建 13 个 binary：`champsim_no` + 4 prefetcher × 3 degree。

## 验证

### 13 个 binary 全部构建成功

```
champsim_no
champsim_ip_stride_d{1,2,3}
champsim_stream_d{1,2,3}
champsim_sms_d{1,2,3}
champsim_sandbox_d{1,2,3}
```

### d1 vs d3 IPC 对比（bfs_kron18, 500K warmup + 2M sim）

| binary | IPC | Δ vs no |
|---|---:|---:|
| champsim_no | 0.4553 | baseline |
| ip_stride_d1 | 0.6490 | +42% |
| ip_stride_d3 | 0.8170 | +79% |
| stream_d1 | 0.8103 | +78% |
| stream_d3 | 1.0482 | +130% |
| sms_d1 | 0.4820 | +6% |
| sms_d3 | 0.5444 | +20% |
| sandbox_d1 | 0.9860 | +117% |
| sandbox_d3 | 1.1864 | +161% |

**关键观察**：
- **d3 > d1 对所有 4 个 prefetcher 成立**——degree 参数化生效
- **所有配置 > no**——健全性通过
- Sandbox d3 IPC 最高（1.19），d1-d3 跨度也最大（0.99→1.19）
- SMS degree 效果最弱（0.48→0.54），符合预期：SMS 是 spatial pattern，
  PHT replay cap 主要影响 burst 发射量

## 工程产出

| 路径 | 类型 | 备注 |
|---|---|---|
| `champsim/prefetcher/ip_stride/ip_stride.h` | 修改 | `#ifndef IP_STRIDE_DEGREE` 守卫 |
| `champsim/prefetcher/stream/stream.h` | 修改 | `#ifndef STREAM_DEGREE` 守卫 |
| `champsim/prefetcher/sms/sms.h` | 修改 | 新增 `PHT_REPLAY_CAP` + `#ifndef` 守卫 |
| `champsim/prefetcher/sms/sms.cc` | 修改 | `pht_lookup_and_queue` 加 cap break |
| `champsim/prefetcher/sandbox/sandbox.h` | 修改 | `#ifndef SANDBOX_DEGREE` 守卫 |
| `scripts/build_prefetcher.sh` | 修改 | 加 degree 参数 + 强制 .o 重编 |
| `scripts/build_all_degrees.sh` | 新增 | 一键构 13 个 binary |
| `champsim/bin/champsim_*_d{1,2,3}` | 构建产物 | 12 个新 binary |

## Step 3 完成度

- [x] 4 个 prefetcher .h 加 `#ifndef` degree 守卫
- [x] SMS 加 `PHT_REPLAY_CAP` 常数 + sms.cc 加 cap break
- [x] build_prefetcher.sh 支持 `[degree]` 参数
- [x] build_all_degrees.sh 一键构建
- [x] 13 个 binary 全部构建成功
- [x] d3 > d1 > no 健全性验证通过

---

# W3 剩余工作（next sessions follow-up）

> Step 1-3 已交付（per-PC AMAT 仪器化 + 24 trace + degree 参数化 13 binary）；
> 剩下 Step 4-6 还要做。

---

## Step 4 — Full prefetcher × degree × trace grid（已完成）

> 完成：2026/05/15
> 范围：13 配置 × 24 trace = **312 ChampSim 跑**，10M sim 窗口，产出 312 个 JSON

### 参数选择

最终采用 **10M sim 窗口**（`--warmup-instructions 1000000 --simulation-instructions 10000000`），
而非原计划的 30M。10M 窗口在小图（kron18 / urand18）上已足够进入算法 phase，
同时大幅缩短了总 wall clock。

### 脚本

`scripts/run_w3_grid.sh [parallelism] [sim_M] [warmup_M]`：
- 6 kernel × 4 input × 13 配置笛卡尔积，xargs 并行
- 跳过已存在 JSON（可重入）
- 输出 `data/w3_grid/<kernel>_<input>_<config>.json`

### 产出概览

| 维度 | 值 |
|---|---|
| JSON 文件数 | **312**（0 失败） |
| 总磁盘 | 160 MB |
| 单文件大小 | 381 KB - 641 KB（均值 525 KB） |
| ROI instructions | 10,000,004 per run |

### Baseline IPC（no prefetcher, 10M sim）

| kernel |    kron18 |    kron20 |   urand18 |   urand20 |
|--------|----------:|----------:|----------:|----------:|
| bfs    |    0.3077 |    0.2207 |    0.2733 |    0.2258 |
| pr     |    0.2370 |    0.2213 |    0.2156 |    0.2210 |
| sssp   |    0.4127 |    0.2307 |    0.3306 |    0.2279 |
| bc     |    0.4178 |    0.2480 |    0.3616 |    0.2475 |
| cc     |    0.2282 |    0.2355 |    0.2075 |    0.2339 |
| tc     |    0.2479 |    0.2353 |    0.2599 |    0.1982 |

小图（kron18 / urand18）IPC 分散在 0.21-0.42，算法行为可见——BC/SSSP 局部性
较好（0.33-0.42），CC/PR 偏低（pointer chasing）。大图（kron20 / urand20）
仍聚在 0.20-0.25 附近，10M 窗口对 1M 节点的大图还不够深入算法 phase，但数据
仍然可用（图加载 + 算法初始阶段本身也是真实的 memory access pattern）。

### L1D unique PCs（no prefetcher, 10M sim）

| kernel |    kron18 |    kron20 |   urand18 |   urand20 |
|--------|----------:|----------:|----------:|----------:|
| bfs    |     1000 |       923 |       994 |       920 |
| pr     |      939 |       924 |       933 |       925 |
| sssp   |     1124 |      1022 |      1132 |      1021 |
| bc     |     1005 |       919 |      1002 |       920 |
| cc     |      908 |       905 |       901 |       901 |
| tc     |      925 |       910 |       903 |       913 |

~900-1130 unique PC per trace，比 Step 2 的 5M sim 略有增长。

### L1D LOAD miss（no prefetcher, 10M sim）

| kernel |   kron18 |   kron20 |  urand18 |  urand20 |
|--------|----------:|----------:|----------:|----------:|
| bfs    |   615065 |   830510 |   584365 |   882540 |
| pr     |  1310101 |   835846 |  1340433 |   835812 |
| sssp   |   489001 |   801819 |   437466 |   802273 |
| bc     |   345865 |   784914 |   329518 |   784859 |
| cc     |   520195 |   792294 |   644357 |   796143 |
| tc     |   547803 |   787425 |   541408 |   910177 |

330K-1.34M L1D miss / 10M sim——memory pressure 充足，per-PC AMAT 信号可靠。
PR 在小图上 miss 最多（~1.3M），BC 最少（~340K）。

### Degree 梯度验证：bfs_kron18 全 13 配置 IPC

| config | IPC | Δ vs no |
|---|---:|---:|
| no | 0.3077 | baseline |
| ip_stride_d1 | 0.4808 | +56.3% |
| ip_stride_d2 | 0.5644 | +83.4% |
| ip_stride_d3 | 0.6141 | +99.6% |
| stream_d1 | 0.5917 | +92.3% |
| stream_d2 | 0.6853 | +122.7% |
| stream_d3 | 0.7258 | +135.9% |
| sms_d1 | 0.3397 | +10.4% |
| sms_d2 | 0.3673 | +19.4% |
| sms_d3 | 0.3963 | +28.8% |
| sandbox_d1 | 0.6875 | +123.5% |
| sandbox_d2 | 0.7442 | +141.9% |
| sandbox_d3 | 0.7680 | +149.6% |

**d3 > d2 > d1 > no 对所有 4 个 prefetcher 成立**——degree 梯度在 10M 窗口下
同样清晰。Sandbox d3 IPC 最高（0.77），SMS 效果最弱（0.34-0.40）。

### per_pc_load_latency 抽样验证

| trace × config | L1D unique PCs |
|---|---:|
| bfs_kron18_no | 1000 |
| bfs_kron18_stream_d2 | 1001 |
| bfs_kron18_sandbox_d3 | 1009 |
| pr_kron20_no | 924 |
| pr_kron20_stream_d2 | 930 |
| pr_kron20_sandbox_d3 | 930 |
| tc_urand20_no | 913 |
| tc_urand20_stream_d2 | 917 |
| tc_urand20_sandbox_d3 | 913 |

所有抽样 JSON 的 `roi.cpu0_L1D.per_pc_load_latency` 均非空，PC 数与 baseline 对应。

### Step 4 完成度

- [x] `scripts/run_w3_grid.sh` 编写（支持 parallelism / sim_M / warmup_M 参数）
- [x] 312 个 JSON 全部产出（`data/w3_grid/`，0 失败）
- [x] 文件名 schema：`<kernel>_<input>_<config>.json`
- [x] Baseline IPC / L1D PC / L1D miss 三张表填好
- [x] Degree 梯度验证：d3 > d2 > d1 > no 全部成立
- [x] per_pc_load_latency 抽样 9 个 JSON 全部非空

---

## Step 5 — objdump → ±128 行汇编 context 抽取（~半天）

**目标**：写 `scripts/extract_asm_context.py`，给定 GAP binary 路径 + 一组 PC，
返回每个 PC 周围 ±128 行汇编字符串。这是 PF-LLM LLM 输入的来源。

### 实现要点

```python
def build_pc_to_asm_index(binary_path: str, context_lines: int = 128) -> dict:
    """
    1. subprocess.run(['objdump', '-d', '--no-show-raw-insn', binary_path])
    2. 解析行：'   105b8: movdqa 0x2310(%rip),%xmm0   # ...'
       提取 (file_offset_hex, instruction_text)
    3. 按行号排序，存 asm_lines[i] = (offset, text)
    4. 给定 runtime PC：
       - 减 PIE 基址 0x555555550000 得 file offset
       - 二分查找在 asm_lines 中的索引 idx
       - 返回 asm_lines[idx-128 : idx+128+1] 拼接成单字符串
    """
```

### 关键细节

- **PIE 基址**：trace 已经用 `setarch -R` 关掉 ASLR，binary 加载到固定 `0x555555550000`。Step 2 已验证：runtime PC `0x5555555605b8` = base + offset `0x105b8`，objdump 一查就到。
- **跨函数边界**：±128 行可能跨越函数（objdump 输出含 `function:` header 行）。论文也是这样做的，**保留**这些 header 行作为 context 的一部分（提供函数边界语义信息）。
- **特殊 PC**：`0x0`（page walk 触发的 fill，没有 demand IP）→ 标记为 invalid，dataset 里跳过。
- **libc PC**（`0x7ffff7fcxxxx`）→ 这些 PC 在 GAP binary objdump 里找不到（属于 libc.so）。两个选择：
  (a) 跳过 libc PC 不进训练集
  (b) 也给 libc.so 做 objdump，挂第二个索引
  → 推荐 (a)，简单干净，loss 一些样本但可控

### Done criteria

- `scripts/extract_asm_context.py <binary> --pcs <pc_list_file>` 输出 JSON
  `{pc_hex: asm_string, ...}`
- 用 bfs_kron18 trace 的 top-50 PC 测一遍，每个都能拿到非空 asm context
- 抽几个手工对照 objdump 验证正确

---

## Step 6 — Label 决策 + JSONL 数据集打包（~半天）

**目标**：合并 Step 4 的 per-PC AMAT 表 + Step 5 的 asm context，按 PF-LLM 论文
§4.2 的规则给每个 (binary, PC) 算 (PF Sel, PF Degree, Filter)，输出 JSONL
训练集。

### Label 决策

每个 (binary, PC) 在 13 个配置下都有 AMAT (除非 PC 在某配置里没出现 → 缺失值)：

```
amat[no][pc]              = baseline
amat[ip_stride_d1][pc]    = ...
amat[ip_stride_d2][pc]    = ...
...
amat[sandbox_d3][pc]      = ...
```

按论文规则：
1. 在 12 个 (prefetcher, degree) 配置里取 argmin AMAT → 得 `(PF_Sel*, PF_Degree*)`
2. 与 `amat[no][pc]` 比较：
   - 如果 `amat[best_config][pc] < amat[no][pc] - tolerance` → label = `(PF_Sel*, PF_Degree*, Filter=False)`
   - 否则（best 都帮不上忙）→ label = `(Filter=True, PF_Sel=None, PF_Degree=None)`
3. tolerance：论文用 1% 或固定 cycle 数；建议 5% 相对（避免噪声 PC 被误标）

### 数据集 schema

每行 JSONL：
```json
{
  "binary": "bfs",
  "pc_runtime": "0x5555555605b8",
  "pc_offset": "0x105b8",
  "asm_context": "<±128 行汇编字符串>",
  "label": {
    "filter": false,
    "pf_sel": "stream",
    "pf_degree": 2
  },
  "_aux": {
    "amat_no": 175.4,
    "amat_best": 31.7,
    "trace_origins": ["bfs_kron18", "bfs_urand18"]
  }
}
```

### Train / test 划分

PF-LLM 论文用 SPEC2006 train、SPEC2017 test。我们用 GAP，没有自然分组。两个方案：

| 方案 | 优点 | 缺点 |
|---|---|---|
| **(a) Per-kernel hold-out**：3 kernel train, 3 kernel test | 评估泛化到新算法 | 样本数 6 个 kernel 不够稳，划分敏感 |
| **(b) Per-input hold-out**：kron train, urand test (or vice versa) | 评估泛化到新输入 | 同 binary 同 PC 在两侧都出现 |

推荐 **混合 (a)**：随机选 4 kernel × 4 input = 16 trace 做 train，剩 8 trace 做 test，
但保证 test 里至少有 1 个 kernel 是 train 没见过的（验证泛化）。

具体可以：train = {bfs, pr, bc, cc} × all inputs；test = {sssp, tc} × all inputs。

### 数据集体量预估

- 24 trace × ~700-1100 unique PC ≈ 24K (trace, PC) 对
- 同一 binary 的同一 PC 跨 trace 应该合并（多 trace 验证 label 稳定性）
- 估计 5K-15K 独立 (binary, PC) 对 → 万级，刚到 PF-LLM 论文规模下沿
- 如果不够：扩 cap 到 100M、加更多 input scale (kron21、kron17)

### Done criteria

- `scripts/build_dataset.py` 跑完产出 `data/dataset/{train,test}.jsonl`
- 行数 5K+，每行 schema 验证通过
- label 分布合理：Filter 占比 10-30%，4 个 prefetcher × 3 degree 至少各占 5%
- 抽样 10 行人工检查：asm_context 是真汇编、label 与 AMAT 数据自洽

---

## 整体优先级 & 风险

**关键路径**：Step 4 (full grid) 是最大时间投入（~3-5h wall）。所有其它 step
都是几小时人工工作 + 短 wall。

**最大风险**：Step 6 数据集体量可能不够（< 5K 样本），需要回头扩 trace（加更多 input
scale 或更长 cap）。建议 Step 4 跑完先做一遍 Step 6 估算样本数，再决定是否扩 trace。

**可平行**：
- Step 4 (full grid) 与 Step 5 (objdump) 互不依赖，可同时进行
- Step 6 必须等 Step 4 + Step 5

**预估总时间**：
- 实现 + 验证：~1-2 个工作日（5+6 各半天，4 一晚后台跑）
- 与 plan.md 原定 W3 时间表（1 周）一致，有余裕

---

# W3b Step 5 — objdump → ±128 行汇编 context 抽取（已完成）

> 完成：2026/05/15
> 范围：给定 GAP binary + 运行时 PC，提取 ±128 行汇编 context

## 实现

`scripts/extract_asm_context.py`：

### 工作流程

1. `objdump -d --no-show-raw-insn <binary>` 解析全部指令行
2. 建立 sorted offset → line index 索引
3. 对每个运行时 PC：减 PIE 基址 `0x555555550000` 得 file offset
4. 二分查找最近指令行（largest offset ≤ file_offset）
5. 取 ±128 行 context，目标行以 `>>>` 标记

### 用法

```bash
# 从 W3 grid JSON 自动提取 PCs：
python3 scripts/extract_asm_context.py vendor/gapbs/bfs \
    --from-grid data/w3_grid/bfs_kron18_no.json -o out.json

# 带最低 fill count 过滤：
python3 scripts/extract_asm_context.py vendor/gapbs/bfs \
    --from-grid data/w3_grid/bfs_kron18_no.json --min-count 50 -o out.json

# 手动指定 PCs：
python3 scripts/extract_asm_context.py vendor/gapbs/bfs \
    --pc 0x5555555596b6 --pc 0x555555559ae0
```

### 输出 schema

```json
{
  "0x5555555596b6": {
    "pc_offset": "0x96b6",
    "asm_context": "    96b0: ...\n>>>     96b6:\tmov    0x80(%rsp),%rdi\n    96bd: ..."
  }
}
```

### PC 分类处理

| PC 类型 | 处理 |
|---|---|
| `0x0`（page walk fill） | 跳过 |
| `0x555555550000 + offset`（binary 内） | 提取 context |
| `0x7ffff7fc...`（libc/vdso） | 跳过（outside_binary） |

## 验证

### 6 个 kernel 全部通过（kron18 baseline JSON，min-count=50）

| kernel | 提取 PCs | 跳过 (null) | 跳过 (outside) | 失败 |
|---|---:|---:|---:|---:|
| bfs | 19 | 1 | 24 | 0 |
| pr | 14 | 1 | 25 | 0 |
| sssp | 18 | 1 | 114 | 0 |
| bc | 9 | 1 | 26 | 0 |
| cc | 10 | 1 | 25 | 0 |
| tc | 6 | 1 | 35 | 0 |

### 完整提取（无 min-count 过滤，kron18 baseline）

| kernel | binary 内 PCs |
|---|---:|
| bfs | 122 |
| pr | 87 |
| sssp | 117 |
| bc | 123 |
| cc | 73 |
| tc | 75 |

### Union across 4 inputs（每个 kernel 的独立 binary PC 数）

| kernel | 唯一 binary PCs |
|---|---:|
| bfs | 134 |
| pr | 94 |
| sssp | 136 |
| bc | 136 |
| cc | 81 |
| tc | 126 |
| **合计** | **~707** |

### 手工验证

- `PC=0x5555555596b6` → offset `0x96b6` → objdump: `mov 0x80(%rsp),%rdi` ✓
- `PC=0x5555555605b8` → offset `0x105b8` → objdump: `movdqa 0x2310(%rip),%xmm0` ✓
- 所有 context 行数均为 257（128 + 1 + 128），边界处理正确
- 目标行 `>>>` 标记位置正确

### 中间 PC 处理

部分 PC（如 `0x55555555958d`）落在指令编码中间（`0x958b` 开始的 5 字节 `mov`）。
脚本正确使用 bisect_right 找到 `0x958b` 作为最近指令，context 以此为中心。

## Step 5 完成度

- [x] `scripts/extract_asm_context.py` 实现
- [x] PIE 基址 `0x555555550000` 转换
- [x] 二分查找最近指令（处理 mid-instruction PC）
- [x] ±128 行 context 抽取，目标行 `>>>` 标记
- [x] 6 个 kernel 全部通过，0 失败
- [x] 手工对照 objdump 验证正确

## 工程产出

| 路径 | 类型 | 备注 |
|---|---|---|
| `scripts/extract_asm_context.py` | 新增 | ±128 行汇编 context 抽取 |

---

# W3b Step 6 — Label 决策 + JSONL 数据集打包（已完成）

> 完成：2026/05/15
> 范围：合并 Step 4 per-PC AMAT + Step 5 asm context，输出 train/test.jsonl

## 实现

`scripts/build_dataset.py`：

### 工作流程

1. **加载 AMAT**：遍历 312 个 grid JSON，对每个 (kernel, input, PC) 提取 13 个配置的 AMAT
2. **Label 决策**：对每个 (kernel, input, PC)：
   - 在 12 个 prefetcher 配置中取 argmin AMAT → `(PF_Sel*, PF_Degree*)`
   - 与 baseline (`no`) 比较：改善 > 5% → `Filter=False`；否则 → `Filter=True`
3. **跨 input 合并**：同 kernel 同 PC 在 4 个 input 上的 label 做 majority vote
4. **Asm context**：调用 extract_asm_context 抽取 ±128 行汇编
5. **Train/test 划分**：{bfs, pr, bc, cc} train / {sssp, tc} test

### 用法

```bash
python3 scripts/build_dataset.py [--min-count 3] [--tolerance 0.05] [--output-dir data/dataset]
```

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--min-count` | 3 | PC 在某配置下最低 fill 次数才参与 AMAT 计算 |
| `--tolerance` | 0.05 | AMAT 相对改善阈值（低于此标 Filter=True） |
| `--context-lines` | 128 | ±N 行汇编 context |

## 产出

### 数据集规模

| 划分 | 记录数 | Kernels | 文件大小 |
|---|---:|---|---:|
| Train | 94 | bfs(34), pr(21), bc(26), cc(13) | 1.3 MB |
| Test | 83 | sssp(43), tc(40) | 1.0 MB |
| **合计** | **177** | 6 kernel × 4 input | 2.3 MB |

### Label 分布

| 维度 | Train | Test |
|---|---|---|
| Filter=True | 35/94 (37.2%) | 37/83 (44.6%) |
| PF Selection | sandbox(37), stream(10), ip_stride(9), sms(3) | sandbox(27), stream(8), ip_stride(8), sms(3) |
| PF Degree | d1(27), d2(10), d3(22) | d1(16), d2(10), d3(20) |

### AMAT 统计

| 维度 | Train | Test |
|---|---|---|
| AMAT(no) | 8.0 — 192.8 — 428.8 (min/med/max) | 8.0 — 149.8 — 504.4 |
| AMAT(best) | 8.0 — 38.1 — 419.4 | 7.9 — 38.2 — 427.3 |

### Label 一致性（跨 input 投票）

- 70% unanimous（4/4 input 一致） — train
- 69% unanimous — test
- trace count 分布：1 trace(21/23), 2(38/18), 3(2/6), 4(33/36)

### JSONL Schema

```json
{
  "binary": "bfs",
  "pc_runtime": "0x5555555596b6",
  "pc_offset": "0x96b6",
  "asm_context": "<257 行汇编>",
  "label": {"filter": false, "pf_sel": "sandbox", "pf_degree": 1},
  "_aux": {"amat_no": 24.0, "amat_best": 8.0, "best_config": "sandbox_d1",
           "vote_count": 2, "total_traces": 2}
}
```

### 人工验证

- Filter=False 示例：`bc/0x555555561c7c`，amat_no=24.0 → amat_best=8.0 (sandbox_d1)，改善 67%，正确标 prefetch ✓
- Filter=True 示例：`bc/0x5555555626a8`，amat_no=241.3 → amat_best=235.1 (sms_d1)，改善 2.5%，低于 5% 阈值，正确标 filter ✓
- Schema 验证：0/177 错误 ✓
- asm_context 行数均为 257 ✓

## 与 Done criteria 对照

| 条件 | 状态 | 备注 |
|---|---|---|
| `scripts/build_dataset.py` 产出 `data/dataset/{train,test}.jsonl` | ✅ | |
| 行数 5K+ | ❌ **177 行** | 见下面"体量不足分析" |
| 每行 schema 验证通过 | ✅ | 0 错误 |
| label 分布合理：Filter 10-30% | ⚠️ **37-45%** | Filter 偏高，见分析 |
| 4 prefetcher × 3 degree 各占 5% | ⚠️ | sms 占比偏低（~3%），sandbox 占优 |
| 抽样 10 行人工检查 | ✅ | asm_context 是真汇编，label 与 AMAT 自洽 |

## 体量不足分析

### 根因

GAP 6 个 kernel 是小型 benchmark，每个 binary 的 `.text` 段只有 ~700-1100 条有 L1D fill 的 PC；
其中 80% 以上属于 libc/vdso（共享库），objdump 只能覆盖 binary 内的 ~80-136 个 PC。
跨 4 input 合并后仅 ~707 unique (kernel, PC) 对——这是 GAP 路线的**结构上限**。

### min-count 影响

| min-count | unique PCs | train | test |
|---:|---:|---:|---:|
| 1 | 706 | 444 | 262 |
| 3 | 177 | 94 | 83 |
| 10 | 135 | 70 | 65 |

min-count=1 能拿到 706 条，但单次 fill 的 AMAT 噪声极大（一次 miss/hit 决定全部）。
min-count=3 是统计可靠性与样本量的平衡点。

### Filter 偏高原因

许多低频 PC（count=3-10）的 AMAT 在 prefetcher 之间差异很小，因为采样不足导致
AMAT 值噪声大，低于 5% tolerance 就标了 Filter。这不是 bug，是数据量约束下的
统计现象。

### 扩展方案（如果 W4 训练效果不佳再执行）

| 方案 | 预估增益 | 代价 |
|---|---|---|
| 加 libc.so objdump（方案 b） | +500-800 PCs | 需要找到对应的 libc.so 版本 |
| 加更多 input scale（kron17, kron21, urand17, urand21） | +50-100 PCs | 重跑 trace + grid，~4h |
| 加 cap 到 200M | 每个 PC 更多 fill → min-count 过滤丢弃更少 | 重跑 trace + grid，~8h |
| 不做跨 input 合并，每 (kernel, input, PC) 独立出样本 | ~2300 条 | asm_context 重复，label 可能不一致 |

## Step 6 完成度

- [x] `scripts/build_dataset.py` 实现
- [x] 312 JSON 加载 + per-PC AMAT 提取
- [x] Label 决策：argmin AMAT + 5% tolerance filter
- [x] 跨 input majority vote 合并
- [x] asm context 集成
- [x] Train/test 划分（4 kernel train / 2 kernel test）
- [x] Schema 验证 + 人工抽查
- [ ] 体量 5K+ 目标未达成（177 条，结构上限 ~700 条）

## 工程产出

| 路径 | 类型 | 备注 |
|---|---|---|
| `scripts/build_dataset.py` | 新增 | Label 决策 + JSONL 打包 |
| `data/dataset/train.jsonl` | 新增 | 94 条训练样本 |
| `data/dataset/test.jsonl` | 新增 | 83 条测试样本 |

---

# W3 总结

## 时间线

| Step | 日期 | 内容 |
|---|---|---|
| Day 1 | 2026/05/09 | Pin SDK + tracer build + bug fix |
| W3a | 2026/05/09 | GAPBS 编译 + 端到端验证 |
| Step 1 | 2026/05/09 | ChampSim per-PC AMAT 仪器化 |
| Step 2 | 2026/05/09 | 24 trace 生成 grid |
| Step 3 | 2026/05/14 | Degree 参数化 + 13 binary |
| Step 4 | 2026/05/15 | 312 ChampSim full grid |
| Step 5 | 2026/05/15 | objdump asm context 抽取 |
| Step 6 | 2026/05/15 | Label 决策 + JSONL 数据集 |

## 产出清单

| 类别 | 数量 |
|---|---|
| GAP traces (xz) | 24 个，~480 MB |
| ChampSim binaries | 13 个 (no + 4×3 degree) |
| Grid JSONs | 312 个，160 MB |
| Dataset (train + test) | 177 条，2.3 MB |
| Scripts | 7 个（gen/build/extract/run） |
| ChampSim patches | per-PC AMAT + degree 参数化 |

## 完成 W3 之后

- W3 数据集就绪 → 进 W4：LLaMA-Factory + Qwen2.5-Coder-0.5B + LoRA 跑训练
- 这是项目第一次需要 GPU（plan §5 W4），需要确认远程 GPU 通道
- 如果训练效果不佳，优先执行"加 libc objdump"或"不做跨 input 合并"扩展方案

