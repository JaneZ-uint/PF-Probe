# W4 — 训练 Pipeline 搭建（CPU 端完成）

> 起始：2026/05/15
> 状态：CPU 端全部完成；等待 GPU 执行训练+评估
> 目标：LLaMA-Factory + Qwen2.5-Coder-0.5B + LoRA 训练管道

---

## W4a — Label 格式修正

### 问题发现

读 PF-LLM 原文 Listing 1 后发现我们 W3 的 label 格式与论文不一致：

| 字段 | 论文格式 | W3 旧格式（错误） |
|---|---|---|
| PF Sel | 永远有值（argmin AMAT 的 prefetcher） | filter=True 时为 null |
| PF Degree | 永远有值（该 prefetcher 的 degree） | filter=True 时为 null |
| Filter | **worst simple prefetcher 名字**（字符串） | **boolean** (true/false) |

论文 Listing 1 Lines 18-22：
```json
{"PF Sel": "stride", "PF Degree": 2, "Filter": "stream"}
```

### 论文的 label 生成规则（§4.1）

1. **PF Sel** = 在所有 (prefetcher, degree) 配置中，AMAT 最低的 prefetcher
2. **PF Degree** = 该最优配置的 degree
3. **Filter** = AMAT 最高（worst）的 **simple** prefetcher 名字
   - 论文规定：若 worst 是 "advanced component" 则不 filter
   - 我们的 advanced = {sms, sandbox}，simple = {ip_stride, stream}
   - 若 worst == best（所有 prefetcher 表现类似）则 Filter = "none"

### 改动

`scripts/build_dataset.py`：
- 新增 `ADVANCED_PREFETCHERS = {"sms", "sandbox"}`
- 重写 `decide_label()`：删除 tolerance 阈值，所有 PC 都出完整 label
- label 字段改为 `{"PF Sel": ..., "PF Degree": ..., "Filter": ...}`

### 效果

| 指标 | 旧格式 | 新格式 |
|---|---:|---:|
| 总样本 | 345 | **456** |
| Train | 172 | **235** |
| Test | 173 | **221** |
| null PF Sel | 有 | **0** |

样本量增加 32%——因为之前被 tolerance 过滤掉的"Filter=True"样本现在都有完整 label。

### 新 label 分布

**Train (235)**：

| PF Sel | sandbox(119) | ip_stride(51) | sms(38) | stream(27) |
|---|---:|---:|---:|---:|
| PF Degree | d1(122) | d3(80) | d2(33) | |
| Filter | none(173) | ip_stride(32) | stream(30) | |

**Test (221)**：

| PF Sel | ip_stride(88) | sandbox(64) | sms(41) | stream(28) |
|---|---:|---:|---:|---:|
| PF Degree | d1(103) | d3(64) | d2(54) | |
| Filter | none(158) | ip_stride(33) | stream(30) | |

---

## W4b — 数据转换（JSONL → LLaMA-Factory sharegpt 格式）

### 文件

| 文件 | 作用 |
|---|---|
| `training/asm_utils.py` | 共享模块：SYSTEM_PROMPT + asm_context 格式化 + label→JSON |
| `training/convert_to_sharegpt.py` | JSONL → sharegpt JSON + dataset_info.json |

### asm_context 格式化规则

原始 objdump 输出：
```
    958b:	mov    %rax,0x68(%rsp)
>>>     959b:	mov    -0x8(%rdi),%rsi
    959f:	jmp    954a <_ZNSt6vector...>
```

转换后（论文格式）：
```
mov    %rax,0x68(%rsp)
<load>mov    -0x8(%rdi),%rsi</load>
jmp    954a <_ZNSt6vector...>
```

变换：
1. 去掉行首地址（`958b:\t` → 空）
2. `>>>` 标记 → `<load>...</load>` 包裹
3. 保留函数头标签（结构信息）
4. 去掉空行（减少 token 数）

### Token 估算

| 指标 | Train | Test |
|---|---:|---:|
| User chars (median) | 8,805 | 9,358 |
| **Est tokens (median)** | **2,201** | **2,339** |
| Est tokens (max) | 3,067 | 2,804 |

`cutoff_len: 4096` 完全覆盖所有样本。

### 验证

- 235 train + 221 test 全部转换成功
- 每条 record 恰好 1 个 `<load>` 标签 ✓
- response 均为合法 JSON ✓
- `data/dataset/dataset_info.json` 已生成 ✓

---

## W4c — LLaMA-Factory 训练配置

`training/train_lora.yaml`：

### 关键参数及理由

| 参数 | 值 | 理由 |
|---|---|---|
| `model_name_or_path` | Qwen/Qwen2.5-Coder-0.5B-Instruct | 与论文一致 |
| `finetuning_type` | lora | 单卡替代论文的 8×H20 全参 |
| `lora_rank` | 16 | 足够捕获 prefetch 分类任务的复杂度 |
| `lora_alpha` | 32 | 2× rank（标准做法） |
| `lora_target` | all | 所有 linear layer，最接近全参 fine-tune |
| `template` | qwen | 自动应用 ChatML `<\|im_start\|>/<\|im_end\|>` |
| `cutoff_len` | 4096 | 覆盖 max ~3100 tokens |
| `train_on_prompt` | **false** | **论文 §5 明确要求**：只在 JSON 输出 token 上算 loss |
| `learning_rate` | 1e-4 | 略高于论文 1e-5（LoRA 需要更大 lr） |
| `num_train_epochs` | 20 | 235 样本 × 20 epochs ÷ 8 batch ≈ 588 gradient updates |
| `gradient_accumulation_steps` | 8 | effective batch = 8 |
| `bf16` | true | 与论文一致 |
| `val_size` | 0.1 | 训练时用 10% 做 sanity check |

### OOM 降级策略

1. `cutoff_len` 4096 → 2048，重跑 `build_dataset.py --context-lines 64`
2. 加 `quantization_bit: 4`（4-bit 量化）
3. `lora_target` → `q_proj,v_proj`（只 LoRA attention）

---

## W4d — 评估脚本

`training/evaluate.py`：

### 工作流程

1. 加载 base model + LoRA adapter + 添加 `<load>`/`</load>` special tokens
2. 对 test JSONL 每条记录：格式化 → apply_chat_template → generate → 解析 JSON
3. 计算指标：parse_rate, pf_sel_acc, pf_degree_acc, filter_acc, joint_acc
4. 输出 JSON with per-sample predictions + metrics

### JSON 解析容错

- 先尝试直接 `json.loads()`
- 失败则用 regex 提取 `{...}` 块，修复 trailing comma
- 仍失败标 `pred=None`

---

## W4e — GPU 环境准备

| 文件 | 作用 |
|---|---|
| `training/requirements_gpu.txt` | pip 依赖（torch, transformers, peft, trl, etc.） |
| `training/setup_gpu.sh` | 一键安装脚本（含 LLaMA-Factory 源码安装 + 模型下载） |

---

## GPU 到手后的三条命令

```bash
# 1. 环境安装（一次性）
bash training/setup_gpu.sh

# 2. 训练
llamafactory-cli train training/train_lora.yaml

# 3. 评估
python3 training/evaluate.py \
    --adapter-path output/pf_llm_lora/checkpoint-500 \
    --dataset data/dataset/test.jsonl \
    --output results/eval.json
```

---

## 工程产出

| 路径 | 类型 | 备注 |
|---|---|---|
| `scripts/build_dataset.py` | 修改 | label 格式对齐论文 Listing 1 |
| `training/asm_utils.py` | 新增 | 共享模块：SYSTEM_PROMPT + 格式化函数 |
| `training/convert_to_sharegpt.py` | 新增 | JSONL → LLaMA-Factory sharegpt 格式 |
| `training/train_lora.yaml` | 新增 | LLaMA-Factory 训练配置 |
| `training/evaluate.py` | 新增 | 评估脚本（加载 LoRA + 推理 + 指标） |
| `training/requirements_gpu.txt` | 新增 | GPU 环境 pip 依赖 |
| `training/setup_gpu.sh` | 新增 | 一键 GPU 安装脚本 |
| `data/dataset/train.jsonl` | 重新生成 | 235 条，新 label 格式 |
| `data/dataset/test.jsonl` | 重新生成 | 221 条，新 label 格式 |
| `data/dataset/train_sharegpt.json` | 新增 | LLaMA-Factory 训练数据 |
| `data/dataset/test_sharegpt.json` | 新增 | LLaMA-Factory 测试数据 |
| `data/dataset/dataset_info.json` | 新增 | LLaMA-Factory 数据注册 |

## 待办

- [ ] 落实 GPU 资源（学校远程 / Colab / AutoDL）
- [ ] 在 GPU 上运行 `setup_gpu.sh`
- [ ] 执行训练 + 评估
- [ ] 根据结果决定是否需要 P1（libc objdump 扩数据）
