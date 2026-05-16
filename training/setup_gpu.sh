#!/bin/bash
# One-shot GPU machine setup for PF-LLM training
# Run from repo root: bash training/setup_gpu.sh
set -euo pipefail

echo "=== PF-LLM GPU Setup ==="

MODEL_PATH="${PF_LLM_MODEL_PATH:-/root/autodl-tmp/models/Qwen2.5-Coder-0.5B-Instruct}"

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

# 3. Verify local model
echo "[3/4] Verifying local Qwen2.5-Coder-0.5B-Instruct..."
if [ ! -f "${MODEL_PATH}/config.json" ]; then
    echo "ERROR: local model not found at ${MODEL_PATH}" >&2
    echo "Set PF_LLM_MODEL_PATH=/path/to/model or copy the model there first." >&2
    exit 1
fi

python3 -c "
from transformers import AutoModelForCausalLM, AutoTokenizer
model_path = '${MODEL_PATH}'
print(f'Loading local model from {model_path}...')
AutoTokenizer.from_pretrained(model_path, trust_remote_code=True, local_files_only=True)
AutoModelForCausalLM.from_pretrained(model_path, trust_remote_code=True, local_files_only=True)
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
    props = torch.cuda.get_device_properties(0)
    total_memory = getattr(props, 'total_memory', getattr(props, 'total_mem', 0))
    print(f'VRAM: {total_memory / 1e9:.1f} GB')
import transformers, peft, accelerate
print(f'Transformers: {transformers.__version__}')
print(f'PEFT: {peft.__version__}')
print(f'Accelerate: {accelerate.__version__}')
"

echo ""
echo "=== Setup complete ==="
echo "To train: llamafactory-cli train training/train_lora.yaml"
echo "To eval:  python3 training/evaluate.py --adapter-path output/pf_llm_lora/checkpoint-XXX"
