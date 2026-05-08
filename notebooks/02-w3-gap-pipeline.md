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
