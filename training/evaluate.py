#!/usr/bin/env python3
"""
Evaluate a PF-LLM LoRA model on the test dataset.

Usage:
  python3 training/evaluate.py \
      --adapter-path output/pf_llm_lora/checkpoint-500 \
      --dataset data/dataset/test.jsonl \
      --output results/eval.json

Requires: torch, transformers, peft (GPU machine only).
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from asm_utils import SYSTEM_PROMPT, asm_context_to_user_prompt, label_to_response


def parse_model_response(text: str) -> dict | None:
    """Try to parse JSON from model output. Returns dict or None."""
    text = text.strip()
    # Try direct parse
    try:
        obj = json.loads(text)
        if "PF Sel" in obj and "PF Degree" in obj and "Filter" in obj:
            return obj
    except json.JSONDecodeError:
        pass

    # Fallback: extract {...} block
    m = re.search(r"\{[^{}]+\}", text)
    if m:
        candidate = m.group(0)
        # Fix trailing comma before }
        candidate = re.sub(r",\s*}", "}", candidate)
        try:
            obj = json.loads(candidate)
            if "PF Sel" in obj and "PF Degree" in obj and "Filter" in obj:
                return obj
        except json.JSONDecodeError:
            pass

    return None


def compute_metrics(predictions: list[dict]) -> dict:
    """Compute accuracy metrics from prediction records."""
    n_total = len(predictions)
    n_parsed = sum(1 for p in predictions if p["pred"] is not None)

    # Filter to parseable predictions for accuracy
    valid = [p for p in predictions if p["pred"] is not None]
    if not valid:
        return {"n_total": n_total, "parse_rate": 0.0}

    pf_sel_correct = sum(1 for p in valid if p["pred"]["PF Sel"] == p["gt"]["PF Sel"])
    degree_correct = sum(1 for p in valid if p["pred"]["PF Degree"] == p["gt"]["PF Degree"])
    filter_correct = sum(1 for p in valid if p["pred"]["Filter"] == p["gt"]["Filter"])
    joint_correct = sum(1 for p in valid
                        if p["pred"]["PF Sel"] == p["gt"]["PF Sel"]
                        and p["pred"]["PF Degree"] == p["gt"]["PF Degree"]
                        and p["pred"]["Filter"] == p["gt"]["Filter"])

    return {
        "n_total": n_total,
        "n_parsed": n_parsed,
        "parse_rate": round(n_parsed / n_total, 4),
        "pf_sel_acc": round(pf_sel_correct / len(valid), 4),
        "pf_degree_acc": round(degree_correct / len(valid), 4),
        "filter_acc": round(filter_correct / len(valid), 4),
        "joint_acc": round(joint_correct / len(valid), 4),
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate PF-LLM LoRA model")
    parser.add_argument("--adapter-path", required=True,
                        help="Path to LoRA adapter checkpoint directory")
    parser.add_argument("--base-model", default="/root/autodl-tmp/models/Qwen2.5-Coder-0.5B-Instruct",
                        help="Base model name or path")
    parser.add_argument("--dataset", default="data/dataset/test.jsonl",
                        help="Test JSONL file path")
    parser.add_argument("--output", default="results/eval.json",
                        help="Output predictions + metrics JSON path")
    parser.add_argument("--max-new-tokens", type=int, default=64,
                        help="Max tokens to generate (default: 64)")
    parser.add_argument("--batch-size", type=int, default=1,
                        help="Inference batch size (default: 1)")
    args = parser.parse_args()

    # Defer heavy imports so --help works without GPU
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    # Load model + adapter
    print(f"Loading base model: {args.base_model}", file=sys.stderr)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)

    # Add special tokens (must match training)
    special_tokens = {"additional_special_tokens": ["<load>", "</load>"]}
    tokenizer.add_special_tokens(special_tokens)

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.resize_token_embeddings(len(tokenizer))

    print(f"Loading adapter: {args.adapter_path}", file=sys.stderr)
    model = PeftModel.from_pretrained(model, args.adapter_path)
    model.eval()

    # Load test data
    with open(args.dataset) as f:
        test_records = [json.loads(line) for line in f]
    print(f"Loaded {len(test_records)} test records", file=sys.stderr)

    # Run inference
    predictions = []
    for i, record in enumerate(test_records):
        user_prompt = asm_context_to_user_prompt(record["asm_context"])
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        prompt = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )

        # Extract only new tokens
        prompt_len = inputs["input_ids"].shape[1]
        new_tokens = outputs[0][prompt_len:]
        response_text = tokenizer.decode(new_tokens, skip_special_tokens=True)

        pred = parse_model_response(response_text)
        gt = record["label"]

        predictions.append({
            "binary": record["binary"],
            "pc_runtime": record["pc_runtime"],
            "pred": pred,
            "gt": gt,
            "raw_response": response_text,
            "correct": pred == gt if pred is not None else False,
        })

        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(test_records)}] processed", file=sys.stderr)

    # Compute metrics
    metrics = compute_metrics(predictions)

    # Per-class breakdown
    from collections import defaultdict
    per_pf_sel = defaultdict(lambda: {"total": 0, "correct": 0})
    for p in predictions:
        if p["pred"] is not None:
            cls = p["gt"]["PF Sel"]
            per_pf_sel[cls]["total"] += 1
            if p["pred"]["PF Sel"] == cls:
                per_pf_sel[cls]["correct"] += 1

    metrics["per_pf_sel"] = {
        k: round(v["correct"] / v["total"], 4) if v["total"] > 0 else 0
        for k, v in per_pf_sel.items()
    }

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {"metrics": metrics, "predictions": predictions}
    with open(output_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Results written to {output_path}", file=sys.stderr)
    print(f"Metrics:", file=sys.stderr)
    for k, v in metrics.items():
        if k != "per_pf_sel":
            print(f"  {k}: {v}", file=sys.stderr)
    print(f"  per_pf_sel: {metrics['per_pf_sel']}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
