# P1 — 补数据与诊断 Baseline

> 起始：2026/05/16
> 状态：P1 第一轮补数据完成；expanded dataset 已同步回本地
> 目标：扩大 PF-LLM 训练/测试覆盖，并补 majority / zero-shot / 分组诊断

---

## 背景

W4 已完成 AutoDL RTX 4090 上的 LoRA 训练与 held-out test 评估。主要结果：

| 指标 | 值 |
|---|---:|
| parse_rate | 1.0000 |
| PF Sel acc | 0.3167 |
| PF Degree acc | 0.3937 |
| Filter acc | 0.6380 |
| Joint acc | 0.1041 |

模型已经稳定输出合法 JSON，但决策质量低于 majority baseline。诊断显示：

- 训练样本只有 235 条，测试样本 221 条。
- 预测分布偏向 `sandbox`、degree 1、`Filter=none`。
- 20 epoch 后训练 loss 很低，但 held-out joint acc 低，存在明显过拟合/覆盖不足。

P1 的核心不是调参，而是先把数据覆盖补起来，并建立更完整的 baseline。

---

## P1a — 新增 GAP 输入图

W3/W4 原始输入：

```text
kron18,kron20,urand18,urand20
```

P1 第一批新增输入：

```text
kron17,kron19,urand17,urand19
```

理由：

1. 在原有 graph family 内增加 scale diversity，最小风险扩样本。
2. `17/19` 正好补在 `18/20` 两侧，避免只扩大到更大图导致 trace/ChampSim 成本飙升。
3. GAP converter 可以本地确定性生成，不依赖外网。

已新增脚本：

| 文件 | 作用 |
|---|---|
| `scripts/gen_gap_inputs.sh` | 根据 `kron<N>` / `urand<N>` 名称生成 `.sg` 和 `.wsg` |
| `scripts/run_p1_data_expansion.sh` | P1 一键入口：图生成 → trace → grid → dataset → ShareGPT |

已生成本地图文件：

```bash
scripts/gen_gap_inputs.sh kron17,kron19,urand17,urand19
```

生成结果：

| 输入 | 节点 | 边（undirected） |
|---|---:|---:|
| kron17 | 131,071 | 1,864,448 |
| kron19 | 524,286 | 7,741,617 |
| urand17 | 131,072 | 2,096,861 |
| urand19 | 524,288 | 8,388,334 |

每个输入均生成：

```text
traces/gap/inputs/<name>.sg
traces/gap/inputs/<name>.wsg
```

其中 `.wsg` 用于 SSSP，其他 kernel 使用 `.sg`。

---

## P1b — 脚本参数化

为了避免继续把 input/kernel 列表写死，以下脚本已经参数化：

| 脚本 | 新能力 |
|---|---|
| `scripts/gen_all_gap_traces.sh` | 支持 `[inputs_csv] [kernels_csv]` |
| `scripts/run_w3_grid.sh` | 支持 `[inputs_csv] [kernels_csv] [out_dir]` |
| `scripts/build_dataset.py` | 支持 `--inputs`、`--kernels`、`--train-kernels`、`--test-kernels` |

兼容性检查：

```bash
python3 scripts/build_dataset.py --output-dir /tmp/pfllm_dataset_check
```

仍得到 W4 原始规模：

| Split | Records |
|---|---:|
| Train | 235 |
| Test | 221 |

说明参数化没有改变默认 W4 数据生成结果。

---

## P1c — 执行补数据

默认一键命令：

```bash
scripts/run_p1_data_expansion.sh
```

等价展开：

```bash
# 1. 生成新增输入图
scripts/gen_gap_inputs.sh kron17,kron19,urand17,urand19

# 2. 只为新增输入生成 Pin traces
scripts/gen_all_gap_traces.sh \
  50 4 \
  kron17,kron19,urand17,urand19 \
  bfs,pr,sssp,bc,cc,tc

# 3. 对全部 8 个输入跑 13-config ChampSim grid
scripts/run_w3_grid.sh \
  8 30 1 \
  kron18,kron20,urand18,urand20,kron17,kron19,urand17,urand19 \
  bfs,pr,sssp,bc,cc,tc

# 4. 重建 JSONL dataset
python3 scripts/build_dataset.py \
  --grid-dir data/w3_grid \
  --inputs kron18,kron20,urand18,urand20,kron17,kron19,urand17,urand19 \
  --kernels bfs,pr,sssp,bc,cc,tc \
  --output-dir data/dataset

# 5. 转为 LLaMA-Factory ShareGPT
python3 training/convert_to_sharegpt.py \
  --train data/dataset/train.jsonl \
  --test data/dataset/test.jsonl \
  --output-dir data/dataset
```

所有步骤都可重入：

- 已存在的 graph 跳过。
- 已存在的 trace 跳过。
- 已存在的 ChampSim JSON 跳过。

---

## P1d — 预期变化

原始 grid：

```text
6 kernels × 4 inputs × 13 configs = 312 ChampSim JSONs
```

P1 扩展后：

```text
6 kernels × 8 inputs × 13 configs = 624 ChampSim JSONs
```

新增工作量：

```text
6 kernels × 4 new inputs × 13 configs = 312 ChampSim JSONs
```

预计 dataset 样本不会严格翻倍，因为样本数取决于每个 trace 中满足 `min-count` 且能映射到 objdump 的 load PCs，但应显著高于 W4 的 456 total records。

---

## P1e — 实际运行结果

### Grid 完整性

远程 AutoDL 20 核 CPU 机器完成全部 expanded grid，结果已同步回本地：

| 项 | 值 |
|---|---:|
| ChampSim JSONs | 624 / 624 |
| 空 JSON 文件 | 0 |
| `data/w3_grid` 大小 | 337 MB |
| `data/dataset` 大小 | 26 MB |

### 构建环境修正

AutoDL Ubuntu 22.04 无法直接运行/链接本地构建产物，处理过程：

1. 远程重新编译 13 个 ChampSim binaries，避免本地 `GLIBC_2.38` 依赖。
2. 清理 `.csconfig`，重写远程 `absolute.options`。
3. 由于远程无网，保留本地同步的 `vcpkg_installed`。
4. 新增 `champsim/src/glibc_compat.cc`，为本地静态库里的 C23 glibc 符号提供 Ubuntu 22.04 兼容转发。

实际验证：

```bash
./champsim/bin/champsim_sms_d2 \
  --warmup-instructions 1000000 \
  --simulation-instructions 30000000 \
  --json /tmp/tc_debug.json \
  traces/gap/tc_kron19.trace.xz
```

能够正常进入 warmup/simulation。

### Expanded Dataset 规模

| Split | W4 | P1 | 增长 |
|---|---:|---:|---:|
| Train | 235 | 686 | 2.92x |
| Test | 221 | 508 | 2.30x |
| Total | 456 | 1194 | 2.62x |

ShareGPT 转换检查：

| Split | Records | Median user chars | Est median tokens | Max est tokens |
|---|---:|---:|---:|---:|
| Train | 686 | 8,586 | 2,146 | 3,058 |
| Test | 508 | 9,159 | 2,289 | 2,781 |

`cutoff_len=4096` 仍覆盖所有样本。

### Train 分布

| 字段 | 分布 | Majority baseline |
|---|---|---:|
| PF Sel | sandbox 343, ip_stride 168, sms 107, stream 68 | 0.5000 |
| PF Degree | d1 344, d3 225, d2 117 | 0.5015 |
| Filter | none 443, ip_stride 128, stream 115 | 0.6458 |

Train kernel/input 覆盖：

| 维度 | 分布 |
|---|---|
| Kernel | bfs 220, bc 192, cc 156, pr 118 |
| Input | urand17 154, kron19 108, kron17 99, kron18 91, urand19 90, urand18 72, kron20 38, urand20 34 |

### Test 分布

| 字段 | 分布 | Majority baseline |
|---|---|---:|
| PF Sel | ip_stride 191, sandbox 182, sms 71, stream 64 | 0.3760 |
| PF Degree | d1 239, d3 151, d2 118 | 0.4705 |
| Filter | none 350, ip_stride 91, stream 67 | 0.6890 |

Joint majority baseline:

```text
("ip_stride", 1, "none") = 122 / 508 = 0.2402
```

Test kernel/input 覆盖：

| 维度 | 分布 |
|---|---|
| Kernel | sssp 293, tc 215 |
| Input | kron17 85, kron19 77, kron18 64, urand19 64, urand17 61, urand18 60, urand20 52, kron20 45 |

### 判断

P1 第一轮补数据达成目标：样本数从 456 增至 1194，且输入 scale 覆盖更连续。下一步可以重新训练 LoRA，但评估标准必须以 expanded test 的 majority baseline 为下限：

| 指标 | Baseline to beat |
|---|---:|
| PF Sel acc | 0.3760 |
| PF Degree acc | 0.4705 |
| Filter acc | 0.6890 |
| Joint acc | 0.2402 |

如果重新训练后仍低于这些 baseline，应优先做 label margin / merge-inputs 降噪，而不是继续单纯加 epoch。

---

## 下一步

- [x] 参数化 P1 数据脚本
- [x] 生成 `kron17,kron19,urand17,urand19` 输入图
- [x] 跑新增 24 条 Pin traces
- [x] 跑新增 312 个 ChampSim JSON
- [x] 重建 expanded dataset + ShareGPT
- [x] 补 majority baseline 统计
- [ ] 补 base model zero-shot / LoRA 对比评估
- [ ] 按 binary、input、label margin 做诊断
- [ ] 用 expanded dataset 重新训练 LoRA
