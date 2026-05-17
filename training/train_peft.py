#!/usr/bin/env python3
"""
Train PF-LLM LoRA without LLaMA-Factory.

This is a fallback for offline GPU machines where LLaMA-Factory cannot be
installed. It uses transformers + peft directly and keeps the key W4/P1
training behavior: prompt tokens are masked, so loss is computed only on the
JSON response.

Usage:
  python3 training/train_peft.py \
    --base-model /root/autodl-tmp/models/Qwen2.5-Coder-0.5B-Instruct \
    --train data/dataset/train.jsonl \
    --output-dir output/pf_llm_lora_p1
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from asm_utils import SYSTEM_PROMPT, asm_context_to_user_prompt, label_to_response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train PF-LLM LoRA with PEFT")
    parser.add_argument("--base-model", default="/root/autodl-tmp/models/Qwen2.5-Coder-0.5B-Instruct")
    parser.add_argument("--train", default="data/dataset/train.jsonl")
    parser.add_argument("--output-dir", default="output/pf_llm_lora_p1")
    parser.add_argument("--epochs", type=float, default=10.0)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--cutoff-len", type=int, default=4096)
    parser.add_argument("--save-steps", type=int, default=100)
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--val-size", type=float, default=0.1)
    parser.add_argument("--num-workers", type=int, default=2)
    return parser.parse_args()


class PFLlmDataset:
    def __init__(self, records: list[dict], tokenizer, cutoff_len: int):
        self.records = records
        self.tokenizer = tokenizer
        self.cutoff_len = cutoff_len

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> dict:
        r = self.records[idx]
        user_prompt = asm_context_to_user_prompt(r["asm_context"])
        response = label_to_response(r["label"])
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        prompt = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        full_text = prompt + response + self.tokenizer.eos_token

        prompt_ids = self.tokenizer(
            prompt, add_special_tokens=False, truncation=True,
            max_length=self.cutoff_len
        )["input_ids"]
        full = self.tokenizer(
            full_text, add_special_tokens=False, truncation=True,
            max_length=self.cutoff_len
        )

        input_ids = full["input_ids"]
        attention_mask = full["attention_mask"]
        labels = input_ids.copy()
        prompt_len = min(len(prompt_ids), len(labels))
        labels[:prompt_len] = [-100] * prompt_len

        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
        }


@dataclass
class CausalCollator:
    tokenizer: object

    def __call__(self, features: list[dict]) -> dict:
        import torch

        max_len = max(len(f["input_ids"]) for f in features)
        pad_id = self.tokenizer.pad_token_id
        batch = {"input_ids": [], "attention_mask": [], "labels": []}
        for f in features:
            pad_len = max_len - len(f["input_ids"])
            batch["input_ids"].append(f["input_ids"] + [pad_id] * pad_len)
            batch["attention_mask"].append(f["attention_mask"] + [0] * pad_len)
            batch["labels"].append(f["labels"] + [-100] * pad_len)
        return {k: torch.tensor(v, dtype=torch.long) for k, v in batch.items()}


def main() -> None:
    args = parse_args()

    import torch
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    set_seed(args.seed)

    with open(args.train) as f:
        records = [json.loads(line) for line in f]

    if args.val_size > 0:
        n_val = max(1, int(len(records) * args.val_size))
        train_records = records[:-n_val]
        val_records = records[-n_val:]
    else:
        train_records = records
        val_records = []

    tokenizer = AutoTokenizer.from_pretrained(
        args.base_model, trust_remote_code=True, local_files_only=True
    )
    special_tokens = {"additional_special_tokens": ["<load>", "</load>"]}
    tokenizer.add_special_tokens(special_tokens)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
        local_files_only=True,
    )
    model.resize_token_embeddings(len(tokenizer))

    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules="all-linear",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    train_dataset = PFLlmDataset(train_records, tokenizer, args.cutoff_len)
    eval_dataset = PFLlmDataset(val_records, tokenizer, args.cutoff_len) if val_records else None

    steps_per_epoch = math.ceil(len(train_dataset) / (args.batch_size * args.grad_accum))
    print(
        f"records={len(records)} train={len(train_dataset)} val={len(val_records)} "
        f"steps_per_epoch~{steps_per_epoch}",
        file=sys.stderr,
    )

    train_kwargs = dict(
        output_dir=args.output_dir,
        overwrite_output_dir=True,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        weight_decay=0.01,
        max_grad_norm=1.0,
        bf16=True,
        logging_steps=args.logging_steps,
        save_steps=args.save_steps,
        save_total_limit=5,
        report_to=[],
        dataloader_num_workers=args.num_workers,
        remove_unused_columns=False,
        seed=args.seed,
    )
    if eval_dataset is not None:
        train_kwargs.update(
            eval_strategy="steps",
            eval_steps=50,
            per_device_eval_batch_size=1,
        )

    training_args = TrainingArguments(**train_kwargs)
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=CausalCollator(tokenizer),
    )

    trainer.train()
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)


if __name__ == "__main__":
    main()
