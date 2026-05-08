# PF-Probe — 复现与扩展研究计划

> **项目名**：PF-Probe（"探针"——剖析 PF-LLM 的设计选择，追问 0.5B LLM 是否必要、汇编 context 哪部分起作用）  
> 机器学习课程期末大作业（个人）  
> 学生：大二本科生，单卡 GPU（学校远程，待定），单学期完成  
> 研究基础：Xu et al., *PF-LLM: Large Language Model Hinted Hardware Prefetching*, ASPLOS'26 Best Paper  
> 撰写日期：2026/05/08

---

## 0. 一句话总览

**研究问题**：在 PF-LLM 把"prefetch 策略选择"建模为静态汇编 → hint 的分类任务后，*0.5B LLM 究竟是不是必需的？哪部分汇编 context 真正提供信息？* 我们用受控的模型族对比 + 表征消融，把这个问题做透，并尝试一个轻量扩展（汇编 context 不可见的 JIT/字节码场景，用 IR 替代）。

**为什么这个角度适合 ML 课程作业**：
- 直接落在课程要求的"分析问题 → 设计实验充分验证"上
- 不与原作者比拼 SOTA（也比不过），但能提出原文没回答的科学问题
- 算力可控：候选模型最大 ~0.5B，单张 24GB GPU 即可
- Mini paper 的"故事"清晰：*Best paper 用 0.5B LLM，是不是因为任务实际比想象的简单？*

---

## 1. 论文要点摘录（自用提示）

| 维度 | PF-LLM 做法 |
|---|---|
| 任务 | 给定一条 load 指令 + 前后 128 行汇编（共 257 行），预测 (PF Sel, PF Degree, Filter) 三元组 |
| 模型 | Qwen2.5-Coder-0.5B-Instruct，全参 fine-tune，2 epochs |
| 标签 | 在 ChampSim 上对每个 (benchmark, sub-prefetcher, degree) 组合各跑一遍，按每个 PC 的 AMAT 取最优；最差的非 advanced prefetcher 作为 Filter |
| 训练资源 | 8× H20，BF16，lr=1e-5，effective batch=64 |
| 训练集 / 测试集 | SPEC2006 训练，SPEC2017 + 真实 web 负载测试 |
| 准确率 | held-out test 95.0% |
| 端到端 | 9.8% IPC↑ over Sandbox（最佳单 prefetcher）；18.9%↑ over Alecto（最佳 ensemble） |
| 硬件代价 | 256-entry PHB（类比 TLB），8-bit hint/PC，PHT 占 binary 大小 ~7.26% |
| 离线代价 | vLLM 234 req/s on 1× H20；整套 SPEC2017 在 8 GPU 上 38.5 min |
| 作者承认的局限 | (a) 不支持 JIT / 字节码（如 Java），需扩到 IR；(b) 不兼容 ASLR；(c) 模型只对一种微架构配置训练 |

---

## 2. 研究问题（Research Questions）

围绕原文留白，提三个相互独立又相互支撑的问题：

- **RQ1（核心）**：**95% 准确率真的需要 LLM 吗？** 一个非 LLM 基线（n-gram + 线性分类器、CodeBERT、小 BiLSTM/Transformer）在同样数据上能拿到多少？性能差距来自模型容量还是预训练知识？
- **RQ2（解释）**：**汇编 context 里哪部分真正承载信息？** 通过表征消融（去掉立即数 / 寄存器名 / 控制流指令 / 不同 context window 长度）、token-level attribution 分析，找出 "load 周围多少行汇编 + 哪些信息" 决定预测。
- **RQ3（扩展，可选）**：**JIT / 字节码场景下能否用 LLVM IR 替代汇编？** 论文 §7.4 列出了这个 limitation 但没做实验。在小 benchmark 上做一个最小可行性证明。

> RQ1 + RQ2 是必做。RQ3 是 stretch goal，时间不足时砍掉。

---

## 3. 算力与 scope 收缩策略

> 单 24GB GPU（推测：4090/3090/A5000/A6000 之一）+ 一台普通 CPU 服务器

**完整复现 PF-LLM 不可行**。原文 SPEC2017 数据集生成需要 *13 个 sub-prefetcher × 各自 degree 范围 ≈ 60+ 组合 × 12 个 benchmark × 200M 指令模拟* — 单机数千 core-hours。我们必须缩 scope：

| 维度 | 原文 | 本项目 |
|---|---|---|
| 训练数据来源 | SPEC2006 全 memory-intensive 子集 | SPEC2017 中选 4 个 memory-intensive：`mcf_s`、`lbm_s`、`omnetpp_s`、`bwaves_s`（其中 1-2 个用于训练，2-3 个 hold out 测试）|
| 模拟长度 | 200M 指令/job | warmup 10M + sim 50M（4× 缩短）|
| sub-prefetcher 池 | 11 种 | 4 种：Stride、Stream、SMS、Sandbox（论文 §6.2 已证明这 4 个就接近 full 配置） |
| Degree 等级 | 1/2/3 三档（标准化） | 不变 |
| 数据集生成总 ChampSim 跑数 | 数百次 | 4 prefetcher × 3 degree × 4 benchmark = **48 次模拟**，每次 ≤ 1.5h，**总 ≤ 72 core-hours**，单机 8 核并行约 1 天 |
| LLM 训练 | 全参 + 8×H20 | LoRA(rank 16-32) on Qwen2.5-Coder-0.5B + bf16，单卡可跑，预计 < 4h/epoch |
| 候选基线模型 | — | (a) char/byte-level n-gram + LogReg；(b) 自训 1-layer Transformer (~5M 参数)；(c) CodeBERT-base 微调 (~110M)；(d) 主角 Qwen-0.5B |

**预期数据集规模**：4 个 benchmark × 每个 benchmark 几千-几万个 unique load PC = **数万条样本**（原文也只在万级），完全够训分类任务。

---

## 4. 技术路线

### 4.1 RQ1 实验设计（模型族对比）

数据集划分：3 个 benchmark 训练 + 1 个 benchmark hold-out 测试（避免 PC 泄漏）；如样本不够再加 cross-benchmark k-fold。

**统一输入**：以 `<load>` 标记的 load 指令 + 前后 128 行汇编（与原文一致）。

| 模型 | 参数量 | 是否预训练 | 训练成本（估） |
|---|---|---|---|
| M0：Most-frequent-class | 0 | — | 几乎为零 |
| M1：mnemonic 1-3gram + TF-IDF + LogReg | ~10K | 否 | < 5 分钟 CPU |
| M2：byte-pair token + 单层 Transformer encoder | ~5M | 否 | < 1h GPU |
| M3：CodeBERT-base 微调（汇编当作文本） | ~110M | 是（自然代码） | ~2h GPU |
| M4：Qwen2.5-Coder-0.5B + LoRA（论文模型） | ~500M（trainable ~5M） | 是（代码 + chat） | ~6h GPU |

**指标**：
- 三个分类头的 top-1 准确率（PF Sel / Degree / Filter，各自独立报）
- joint accuracy（三项全对）
- "second-best 容忍准确率"：原文 §6.1 提到 mispredict 多落到第二好选项，复现这个指标
- ChampSim 端到端 IPC（仅对 M1 / M3 / M4 做，因为太花时间）

**预期发现**（假设）：M1（n-gram）显著低于 M3/M4，但 M3 已接近 M4 → 说明任务靠"代码语义先验"而非"模型规模"；如 M4 显著优于 M3，则 reinforces "需要 LLM"。

### 4.2 RQ2 实验设计（表征消融 + 可解释性）

固定 M4，做以下消融：

1. **Context 长度**：±16 / ±32 / ±64 / ±128 行（默认）；找 accuracy 拐点
2. **Token mask**：屏蔽（替成 `<MASK>` 或随机化）
   - 立即数（地址、常量）
   - 寄存器名（统一替成 `regK`）
   - 非内存访问指令（保留 mov/load/store，去掉 add/cmp/jmp）
   - 函数边界 / call 指令
3. **位置敏感性**：把 load 周围 ±N 行随机打乱，看准确率退化曲线
4. **Saliency 分析**：用 input-erasure / attention rollout，可视化模型最依赖的 token 类别（指令名 vs 寄存器 vs 立即数 vs 控制流）。生成 1-2 张定性 case study 图

**预期产出**：一张 *"什么信息让 LLM 学会 prefetch"* 的清晰描述。这是原文没做、ML 课程评委会喜欢的"分析"。

### 4.3 RQ3 实验设计（IR 替代汇编，stretch goal）

对训练集中的同一批 benchmark，用 `clang -S -emit-llvm` 同时生成 LLVM IR；在 IR 中识别每条 load（按调试信息映射回汇编 PC），用相同的 ±128 IR 行 context 重训 M4-IR。

**对比**：M4-asm vs M4-IR 在同 hold-out benchmark 上的准确率与 IPC。

**意义**：若 IR 不掉点，意味着论文的 limitation #1（JIT/字节码）可由 IR 输入解决。

> 风险：IR ↔ 汇编的 PC 映射调试可能耗时。预算 1 周，超时则 RQ3 改为定性讨论。

---

## 5. 时间表（约 14 周，假设第 1 周就开工）

| 周次 | 阶段 | 关键产出 |
|---|---|---|
| W1 | 环境 | ChampSim DPC-3 编译跑通；下载 SPEC2017 simpointed traces；选定 4 个 benchmark；工具链记录在 `notebooks/00-env.md` |
| W2 | 子预取器 | 在 ChampSim 中确认 Stride / Stream / SMS / Sandbox 四件套可用（DPC-3 主分支自带 Stride/Stream，SMS/Sandbox 需从 Pythia 等 repo 移植）；no-prefetch / 单 prefetcher baseline 跑通 |
| W3 | 数据集生成 | 修改 ChampSim 输出每 PC 的 AMAT；并行跑 48 次模拟；导出 `<asm_context, label>` JSONL 数据集 |
| W4 | 训练 pipeline | LLaMA-Factory + Qwen-0.5B + LoRA 跑通；复现单 benchmark 上 ≥ 80% 准确率；记入 `notebooks/04-baseline-llm.ipynb` |
| W5 | RQ1 — 弱基线 | 实现 M0/M1/M2，跑全数据集 |
| W6 | RQ1 — 强基线 | 实现 M3 (CodeBERT)、调整 M4，统一 eval；产出五个模型的对比表 |
| W7 | RQ1 — 端到端 | 选 M1 / M3 / M4 三档跑 ChampSim 端到端 IPC；产出主结果图 |
| W8 | RQ2 — context 长度 + token mask | 固定 M4，跑约 8-10 个消融 config |
| W9 | RQ2 — 可解释性 | 实现 input-erasure saliency；生成 case study |
| W10 | RQ3 (stretch) | LLVM IR 抽取 + 训练 M4-IR；如卡壳，转为讨论章节 |
| W11 | 写作启动 | mini paper 初稿（intro / method） |
| W12 | 写作 + 复跑 | 补漏实验，整理图表 |
| W13 | 写作 | 二稿；poster 设计 |
| W14 | 收尾 | poster 印制；代码 release（README + 一键脚本） |

> 缓冲：W3 和 W7 各预留 2 天 slack，因为 ChampSim 跑模拟最容易超时。

---

## 6. 关键工程清单

- **ChampSim**：[https://github.com/ChampSim/ChampSim](https://github.com/ChampSim/ChampSim) 主分支。修改点：在每个 cache miss/hit 处累加 AMAT 并按 PC 输出
- **Trace**：DPC-3 提供的 SPEC2017 simpointed traces（约 20GB，单 benchmark 1-2GB）
- **Sub-prefetcher 实现参考**：Pythia repo（[CMU-SAFARI/Pythia](https://github.com/CMU-SAFARI/Pythia)）已经移植了 Stride/Stream/SMS/Sandbox 等多种 prefetcher，可直接借用
- **训练框架**：LLaMA-Factory（与原文一致）+ DeepSpeed off（单卡）；序列长 1024-2048；上下文格式严格按 Listing 1 复刻
- **推理**：vLLM 单卡足以；本项目数据量小（万级 load），全部推理几分钟内
- **可解释性**：`captum` 或自己写 input-erasure；attention 可视化用 `bertviz`

---

## 7. 风险与备选

| 风险 | 应对 |
|---|---|
| ChampSim trace 跑得比预想慢 | 进一步缩到 sim 30M、benchmark 缩到 3 个；并行度用满 8 核 |
| SMS / Sandbox 移植困难 | 退化到 Stride / Stream / Pythia / Bingo 四件套（Pythia repo 现成） |
| LoRA 训练无法收敛到 80%+ | 检查 prompt 模板（Qwen 必须用官方 chat template）、loss mask（只在 JSON 输出 token 上算 loss）；这两个是原文反复强调的关键细节 |
| 单卡显存不够 | 截短 context 到 ±64 行；或换 Qwen2.5-Coder-0.5B 的非 Instruct base 版本 |
| 数据量太少 M4 过拟合 | 加 dropout、降 LoRA rank、加正则；或用 cross-benchmark CV |
| RQ3 时间用光 | 仅做定性讨论，不做实验，明确写入 limitation |

---

## 8. 交付物清单

- [ ] `paper/main.pdf` — mini paper（建议 6-8 页双栏，含 RQ / Method / Experiments / Discussion / Limitations）
- [ ] `code/` — 含 `champsim/`、`data_pipeline/`、`training/`、`analysis/` 四个子目录，README 写一键复现
- [ ] `poster/poster.pdf` — A1 海报
- [ ] `notebooks/` — 关键实验的 reproducible notebook
- [ ] `models/` — 至少 M1、M3、M4 三个 checkpoint（M4 可只放 LoRA adapter）

---

## 9. 写作角度建议（mini paper）

不必声称"我们超越了 PF-LLM"。更好的故事：

> *"PF-LLM achieves 95% accuracy with a 0.5B LLM. We ask: was the LLM necessary? Through a controlled comparison from 10K-parameter n-gram baselines to 0.5B Qwen, plus a representation ablation on the assembly context, we find that **most of the accuracy is attributable to the code-aware tokenization and a moderate-capacity transformer**, while the marginal gain from 0.5B comes from generalization on rare opcodes. We also show that LLVM IR is a viable input for JIT scenarios. These results sharpen the design space for LLM-hinted microarchitecture."*

这个 framing：
- 不与原作者正面冲突（complementary，不是 contradiction）
- 让 ML 课程评委看到清楚的 *科学假设 → 实验证伪* 流程
- 给后续工作留接口（"什么时候 LLM 真的有用"）

---

## 10. Open Questions（边做边记）

- 不同 benchmark 的最优策略分布差异有多大？（影响 cross-benchmark 泛化）
- 是否能拿到原作者的 Qwen-PF-LLM checkpoint 直接复用？目前论文没有 release 信息，需要持续关注作者主页
- M1 (n-gram) 在哪种 load pattern 上彻底失败？这是 LLM 真正不可替代的领域
