# W1 — 环境搭建日志

> 起始：2026/05/08  
> 目标：跑通 ChampSim baseline，确认整条数据 pipeline 可工作

## 系统基线（host）
- WSL2 Ubuntu 24.04 on Windows，kernel 6.6.87.2
- CPU: Intel Core Ultra 7 155H, 22 cores
- RAM: 15GB（仿真够用，训练不行）
- Disk: 851GB free
- **GPU: 无可用** — WSL 没装 CUDA passthrough，宿主 Windows 也无 NVIDIA driver
- gcc/g++ 13.3, cmake 3.28, GNU make 4.3, Python 3.13 (anaconda)

> 训练 GPU 路线：用学校 / 实验室远程机（待确认卡型）。本地仅做数据 pipeline + ChampSim 仿真 + 写代码。

## ChampSim
- 仓库：https://github.com/ChampSim/ChampSim 主分支（master）；commit `06de8d3`
- 克隆方式：HTTP 极慢（卡在 8KB），改用 SSH 几秒搞定
- 已 clone 到 `champsim/`
- 依赖：vcpkg manifest 列出 cli11 / nlohmann-json / fmt / bzip2 / liblzma / zlib / catch2
- vcpkg：浅克隆 + bootstrap 通过 + `./vcpkg/vcpkg install` 1.9 分钟全部完成（构建产物在 `champsim/vcpkg_installed/x64-linux/`）

### 踩到的坑
1. **CRLF**：全局 `core.autocrlf = true`（Windows 默认）会把 `config.sh` 改成 CRLF，运行时 `/usr/bin/env: 'python3\r': No such file...`。改用 `core.autocrlf = input` + `git reset --hard` 重新 checkout 解决。
2. HTTP 克隆 vcpkg 卡住（13839 个文件，国内拉巨慢），改 SSH 立刻通。

### 编译
```bash
./config.sh champsim_config.json   # 生成 .csconfig/
make -j8                            # ~1 分钟，产出 bin/champsim (1.9MB)
```

## ChampSim 主分支自带的 prefetcher
位于 `champsim/prefetcher/`：

| 名字 | 用途 | 我们用得上 |
|---|---|---|
| `no` | 不预取 | ✓ baseline |
| `next_line` | 取下一行 | 简单基线 |
| `ip_stride` | PC-indexed stride | ✓（=plan 里的 Stride） |
| `va_ampm_lite` | 虚地址 AMPM 精简版 | 可选 |
| `spp_dev` | Signature Path Prefetcher | 不在我们 4 件套，可作对比 |

**缺：Stream / SMS / Sandbox**。要从 [CMU-SAFARI/Pythia](https://github.com/CMU-SAFARI/Pythia) 移植，列入 W2。

## SPEC2017 traces
- 来源：https://dpc3.compas.cs.stonybrook.edu/champsim-traces/speccpu/
- 已下载 `605.mcf_s-1554B.champsimtrace.xz`（159MB）做 smoke test
- 待下：lbm / omnetpp / bwaves（脚本 `scripts/download_traces.sh` 已写好）
- 总计预估 ~700MB

## Smoke test 结果（mcf, 1M warmup + 2M sim）

| Prefetcher | IPC | Δ |
|---|---|---|
| `no` | 0.1237 | baseline |
| `ip_stride` | 0.1380 | **+11.6%** |

> 2M 指令仿真用 ~1m45s。 mcf 是 pointer-chasing 极差 case，绝对 IPC 低正常。L1D miss rate 41%，LLC miss rate 74%，确实是 memory-bound。
>
> 10M 指令仿真用 ~9 分钟（外推：50M 指令 ≈ 45 分钟/run。22 核机并行，48 个 sim job 约 4-5 小时跑完——和 plan §3 估算一致）。

## 关键工程发现：JSON 输出没有 per-PC AMAT

ChampSim `--json` 输出 schema：
- `roi.cores[].instructions`, `roi.cores[].cycles` → 算 IPC
- `roi.cpu0_L1D.LOAD.{hit,miss,mshr_merge}` → cache 整体统计
- `roi.LLC.*`, `roi.DRAM.*` → 内存层
- **没有按 PC 分桶的 AMAT/miss latency**

论文 §4.2 写"我们修改 ChampSim 让它输出每个 PC 的 AMAT"，**这是要自己写 patch 的**。属于 W3 数据集生成的核心工作量，不是 W1 范围。

W3 的具体改动（占位）：
- 在 `cpu0_L1D` cache miss 处理路径上，给每个 in-flight miss 记录 PC 与发起 cycle
- 在 fill 完成时计算 `latency = fill_cycle - issue_cycle`，按 PC 累加
- 退出时 dump per-PC histogram 到额外 JSON

可以在 `src/cache.cc` 的 `add_miss_handler` / `handle_fill` 附近改。先看这里再说。

## W1 收尾状态

- [x] vcpkg install 完成
- [x] `./config.sh && make` 通过
- [x] 1 条 trace、no-prefetch baseline IPC=0.124
- [x] 1 条 trace、ip_stride IPC=0.138（+11.6%）
- [x] 验证：通过 JSON 配置切换 prefetcher 可以重新生成 binary 并跑出不同 IPC
- [x] 下载剩余 3 条 trace（W2 时同步完成；mcf 159M / lbm 759M / omnetpp 766M / bwaves 34M）
- [ ] 学校 GPU 信息确认（待用户答复）

→ W2 转入 [01-w2-prefetchers.md](01-w2-prefetchers.md)：移植 Stream / SMS / Sandbox 三个
prefetcher、跑通 4 件套 baseline。
