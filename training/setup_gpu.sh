#!/bin/bash
# One-shot GPU machine setup for PF-LLM training
# Run from repo root: bash training/setup_gpu.sh
set -euo pipefail

echo "=== PF-LLM GPU Setup ==="

# 1. Python deps
echo "[1/4] Installing Python dependencies..."
pip install -r training/requirements_gpu.txt

# 2. LLaMA-Factory
if ! python3 -c "import llamafactory" 2>/dev/null; then
    echo "[2/4] Installing LLaMA-Factory from source..."
    git clone --depth 1 https://github.com/hiyouga/LLaMA-Factory /tmp/LLaMA-Factory
    cd /tmp/LLaMA-Factory && pip install -e ".[torch,metrics]"
    cd -
else
    echo "[2/4] LLaMA-Factory already installed, skipping"
fi

# 3. Download model
echo "[3/4] Downloading Qwen2.5-Coder-0.5B-Instruct..."
python3 -c "
from transformers import AutoModelForCausalLM, AutoTokenizer
model_name = 'Qwen/Qwen2.5-Coder-0.5B-Instruct'
print(f'Downloading {model_name}...')
AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
AutoModelForCausalLM.from_pretrained(model_name, trust_remote_code=True)
print('Done.')
"

# 4. Verify
echo "[4/4] Verifying setup..."
python3 -c "
import torch
print(f'PyTorch: {torch.__version__}')
print(f'CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'GPU: {torch.cuda.get_device_name(0)}')
    print(f'VRAM: {torch.cuda.get_device_properties(0).total_mem / 1e9:.1f} GB')
import transformers, peft, accelerate
print(f'Transformers: {transformers.__version__}')
print(f'PEFT: {peft.__version__}')
print(f'Accelerate: {accelerate.__version__}')
"

echo ""
echo "=== Setup complete ==="
echo "To train: llamafactory-cli train training/train_lora.yaml"
echo "To eval:  python3 training/evaluate.py --adapter-path output/pf_llm_lora/checkpoint-XXX"
