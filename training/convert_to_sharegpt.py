#!/usr/bin/env python3
"""
Convert PF-LLM JSONL dataset to LLaMA-Factory sharegpt format.

Usage:
  python3 training/convert_to_sharegpt.py \
      --train data/dataset/train.jsonl \
      --test  data/dataset/test.jsonl \
      --output-dir data/dataset

Outputs:
  data/dataset/train_sharegpt.json
  data/dataset/test_sharegpt.json
  data/dataset/dataset_info.json  (LLaMA-Factory registration)
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from asm_utils import SYSTEM_PROMPT, asm_context_to_user_prompt, label_to_response


def convert_file(input_jsonl: Path, output_json: Path) -> dict:
    """Convert a JSONL file to sharegpt JSON. Returns stats dict."""
    records = []
    with open(input_jsonl) as f:
        for line in f:
            r = json.loads(line)
            user_prompt = asm_context_to_user_prompt(r["asm_context"])
            response = label_to_response(r["label"])

            records.append({
                "conversations": [
                    {"from": "human", "value": user_prompt},
                    {"from": "gpt", "value": response},
                ],
                "system": SYSTEM_PROMPT,
            })

    with open(output_json, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # Compute stats
    user_lens = [len(r["conversations"][0]["value"]) for r in records]
    resp_lens = [len(r["conversations"][1]["value"]) for r in records]

    return {
        "n_records": len(records),
        "user_chars": {
            "min": min(user_lens),
            "median": sorted(user_lens)[len(user_lens) // 2],
            "max": max(user_lens),
        },
        "resp_chars": {
            "min": min(resp_lens),
            "median": sorted(resp_lens)[len(resp_lens) // 2],
            "max": max(resp_lens),
        },
        "est_tokens": {
            "min": min(user_lens) // 4,
            "median": sorted(user_lens)[len(user_lens) // 2] // 4,
            "max": max(user_lens) // 4,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Convert PF-LLM JSONL to LLaMA-Factory sharegpt format")
    parser.add_argument("--train", default="data/dataset/train.jsonl",
                        help="Train JSONL path")
    parser.add_argument("--test", default="data/dataset/test.jsonl",
                        help="Test JSONL path")
    parser.add_argument("--output-dir", default="data/dataset",
                        help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Convert train
    train_out = output_dir / "train_sharegpt.json"
    print(f"Converting {args.train} → {train_out}...", file=sys.stderr)
    train_stats = convert_file(Path(args.train), train_out)
    print(f"  {train_stats['n_records']} records, "
          f"user chars: {train_stats['user_chars']}, "
          f"est tokens: {train_stats['est_tokens']}", file=sys.stderr)

    # Convert test
    test_out = output_dir / "test_sharegpt.json"
    print(f"Converting {args.test} → {test_out}...", file=sys.stderr)
    test_stats = convert_file(Path(args.test), test_out)
    print(f"  {test_stats['n_records']} records, "
          f"user chars: {test_stats['user_chars']}, "
          f"est tokens: {test_stats['est_tokens']}", file=sys.stderr)

    # Write dataset_info.json for LLaMA-Factory
    dataset_info = {
        "pf_llm_train": {
            "file_name": "train_sharegpt.json",
            "formatting": "sharegpt",
            "columns": {"messages": "conversations", "system": "system"},
        },
        "pf_llm_test": {
            "file_name": "test_sharegpt.json",
            "formatting": "sharegpt",
            "columns": {"messages": "conversations", "system": "system"},
        },
    }
    info_path = output_dir / "dataset_info.json"
    with open(info_path, "w") as f:
        json.dump(dataset_info, f, indent=2)
    print(f"Written {info_path}", file=sys.stderr)

    # Spot-check: show first record
    print(f"\n{'='*60}", file=sys.stderr)
    print("Spot-check (first train record):", file=sys.stderr)
    with open(train_out) as f:
        first = json.load(f)[0]
    user_lines = first["conversations"][0]["value"].split("\n")
    # Find <load> line
    for i, line in enumerate(user_lines):
        if "<load>" in line:
            start = max(0, i - 3)
            end = min(len(user_lines), i + 4)
            print(f"  ... (line {start})", file=sys.stderr)
            for j in range(start, end):
                print(f"  {user_lines[j]}", file=sys.stderr)
            print(f"  ... (line {end})", file=sys.stderr)
            break
    print(f"  Response: {first['conversations'][1]['value']}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
