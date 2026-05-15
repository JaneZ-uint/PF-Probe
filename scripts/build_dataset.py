#!/usr/bin/env python3
"""
W3b Step 6 — Build PF-LLM training dataset from W3 grid results.

Merges:
  - Step 4: per-PC AMAT from 312 ChampSim JSONs (data/w3_grid/)
  - Step 5: ±128-line assembly context from objdump (scripts/extract_asm_context.py)

Outputs:
  - data/dataset/train.jsonl
  - data/dataset/test.jsonl

Usage:
  python3 scripts/build_dataset.py [--grid-dir data/w3_grid] [--min-count 3] [--tolerance 0.05]

By default emits one record per (kernel, input, PC) — same binary PC across different
inputs becomes separate samples. Use --merge-inputs to majority-vote merge across inputs
(produces fewer but more confident labels).
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Re-use extract_asm_context internals
sys.path.insert(0, str(Path(__file__).parent))
from extract_asm_context import PIE_BASE, parse_objdump, build_offset_index, find_context

KERNELS = ["bfs", "pr", "sssp", "bc", "cc", "tc"]
INPUTS = ["kron18", "kron20", "urand18", "urand20"]
CONFIGS = [
    "no",
    "ip_stride_d1", "ip_stride_d2", "ip_stride_d3",
    "stream_d1", "stream_d2", "stream_d3",
    "sms_d1", "sms_d2", "sms_d3",
    "sandbox_d1", "sandbox_d2", "sandbox_d3",
]
PREFETCHER_CONFIGS = CONFIGS[1:]  # exclude "no"

TRAIN_KERNELS = {"bfs", "pr", "bc", "cc"}
TEST_KERNELS = {"sssp", "tc"}

BINARY_DIR = Path("vendor/gapbs")


def parse_config(config: str) -> tuple[str, int]:
    """Parse 'stream_d2' -> ('stream', 2)."""
    parts = config.rsplit("_d", 1)
    return parts[0], int(parts[1])


def load_per_pc_amat(grid_dir: Path, kernel: str, inp: str, config: str,
                     min_count: int) -> dict[str, tuple[float, int]]:
    """Load per-PC AMAT from a single grid JSON. Returns {pc_hex: (amat, count)}."""
    path = grid_dir / f"{kernel}_{inp}_{config}.json"
    if not path.exists():
        return {}
    with open(path) as f:
        d = json.load(f)
    ppc = d[0]["roi"]["cpu0_L1D"]["per_pc_load_latency"]
    result = {}
    for pc_hex, v in ppc.items():
        count = v["count"]
        if count < min_count:
            continue
        result[pc_hex] = (v["sum"] / count, count)
    return result


def is_binary_pc(pc_hex: str) -> bool:
    """Check if PC is within the GAP binary (not libc/vdso/null)."""
    pc_val = int(pc_hex, 16)
    if pc_val == 0:
        return False
    offset = pc_val - PIE_BASE
    return 0 < offset < 0x100000


def decide_label(amat_by_config: dict[str, float], tolerance: float
                 ) -> dict:
    """Decide (PF_Sel, PF_Degree, Filter) from per-config AMAT values.

    Returns dict with keys: filter, pf_sel, pf_degree, amat_no, amat_best, best_config.
    """
    amat_no = amat_by_config.get("no")
    if amat_no is None:
        return None

    # Find best prefetcher config
    best_config = None
    best_amat = float("inf")
    for config in PREFETCHER_CONFIGS:
        if config in amat_by_config:
            a = amat_by_config[config]
            if a < best_amat:
                best_amat = a
                best_config = config

    if best_config is None:
        return None

    # Filter decision: is the best prefetcher meaningfully better than no-prefetch?
    improvement = (amat_no - best_amat) / amat_no if amat_no > 0 else 0

    if improvement > tolerance:
        pf_sel, pf_degree = parse_config(best_config)
        return {
            "filter": False,
            "pf_sel": pf_sel,
            "pf_degree": pf_degree,
            "amat_no": round(amat_no, 2),
            "amat_best": round(best_amat, 2),
            "best_config": best_config,
        }
    else:
        return {
            "filter": True,
            "pf_sel": None,
            "pf_degree": None,
            "amat_no": round(amat_no, 2),
            "amat_best": round(best_amat, 2),
            "best_config": best_config,
        }


def majority_label(labels: list[dict]) -> dict:
    """Given labels from multiple (kernel, input) traces for the same (kernel, PC),
    pick the majority label. For ties, pick the one with lowest amat_best."""
    # Group by (filter, pf_sel, pf_degree) tuple
    groups = defaultdict(list)
    for lb in labels:
        key = (lb["filter"], lb["pf_sel"], lb["pf_degree"])
        groups[key].append(lb)

    # Pick the group with most votes; break ties by lowest avg amat_best
    best_key = max(groups.keys(),
                   key=lambda k: (len(groups[k]),
                                  -min(lb["amat_best"] for lb in groups[k])))
    winner_labels = groups[best_key]

    # Aggregate aux info
    avg_amat_no = sum(lb["amat_no"] for lb in labels) / len(labels)
    avg_amat_best = sum(lb["amat_best"] for lb in labels) / len(labels)

    return {
        "filter": best_key[0],
        "pf_sel": best_key[1],
        "pf_degree": best_key[2],
        "amat_no": round(avg_amat_no, 2),
        "amat_best": round(avg_amat_best, 2),
        "best_config": winner_labels[0]["best_config"],
        "vote_count": len(winner_labels),
        "total_traces": len(labels),
    }


def main():
    parser = argparse.ArgumentParser(description="Build PF-LLM dataset from W3 grid")
    parser.add_argument("--grid-dir", default="data/w3_grid", help="W3 grid JSON directory")
    parser.add_argument("--min-count", type=int, default=10,
                        help="Min fill count per PC to include (default: 10)")
    parser.add_argument("--tolerance", type=float, default=0.05,
                        help="Relative AMAT improvement threshold for Filter decision (default: 0.05)")
    parser.add_argument("--context-lines", type=int, default=128,
                        help="Assembly context lines (default: 128)")
    parser.add_argument("--merge-inputs", action="store_true",
                        help="Merge same (kernel, PC) across inputs via majority vote "
                             "(default: emit one record per (kernel, input, PC))")
    parser.add_argument("--output-dir", default="data/dataset", help="Output directory")
    args = parser.parse_args()

    grid_dir = Path(args.grid_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Phase 1: Load all per-PC AMAT data ──────────────────────────────
    print("Phase 1: Loading per-PC AMAT from grid JSONs...", file=sys.stderr)

    # per_trace_labels[(kernel, input, pc_hex)] = label_dict
    per_trace_labels = {}
    # Also group by (kernel, pc_hex) for merge mode
    per_kernel_pc_labels = defaultdict(list)

    total_loaded = 0
    for kernel in KERNELS:
        for inp in INPUTS:
            # Load AMAT for all 13 configs for this (kernel, input)
            amat_all = {}  # config -> {pc_hex: (amat, count)}
            for config in CONFIGS:
                amat_all[config] = load_per_pc_amat(
                    grid_dir, kernel, inp, config, args.min_count)
            total_loaded += 13

            # Get all binary-internal PCs seen in the baseline
            baseline_pcs = set(amat_all["no"].keys())

            for pc_hex in baseline_pcs:
                if not is_binary_pc(pc_hex):
                    continue

                # Build per-config AMAT for this PC
                amat_by_config = {}
                for config in CONFIGS:
                    if pc_hex in amat_all[config]:
                        amat_by_config[config] = amat_all[config][pc_hex][0]

                label = decide_label(amat_by_config, args.tolerance)
                if label is not None:
                    per_trace_labels[(kernel, inp, pc_hex)] = label
                    per_kernel_pc_labels[(kernel, pc_hex)].append(label)

    print(f"  Loaded {total_loaded} JSONs", file=sys.stderr)

    # ── Phase 2: Build sample list ──────────────────────────────────────
    if args.merge_inputs:
        print("Phase 2: Merging labels across inputs (majority vote)...",
              file=sys.stderr)
        samples = {}  # (kernel, pc_hex) -> record_info
        for (kernel, pc_hex), labels in per_kernel_pc_labels.items():
            ml = majority_label(labels)
            samples[(kernel, pc_hex)] = {
                "label": ml,
                "input": None,  # merged
            }
        print(f"  {len(samples)} unique (kernel, PC) pairs", file=sys.stderr)
    else:
        print(f"Phase 2: Per-(kernel, input, PC) mode — "
              f"{len(per_trace_labels)} samples...", file=sys.stderr)
        samples = {}
        for (kernel, inp, pc_hex), label in per_trace_labels.items():
            # Add trace-level aux info
            label["vote_count"] = 1
            label["total_traces"] = 1
            samples[(kernel, inp, pc_hex)] = {
                "label": label,
                "input": inp,
            }

    # ── Phase 3: Extract assembly context ───────────────────────────────
    print("Phase 3: Extracting assembly context...", file=sys.stderr)

    # Group PCs by kernel to avoid re-running objdump
    pcs_by_kernel = defaultdict(set)
    for key in samples:
        kernel = key[0]
        pc_hex = key[-1]
        pcs_by_kernel[kernel].add(pc_hex)

    asm_cache = {}  # (kernel, pc_hex) -> {"pc_offset": ..., "asm_context": ...}

    for kernel in KERNELS:
        binary_path = str(BINARY_DIR / kernel)
        pcs = pcs_by_kernel[kernel]
        if not pcs:
            continue

        print(f"  Disassembling {kernel}...", file=sys.stderr)
        asm_lines = parse_objdump(binary_path)
        instr_line_indices = build_offset_index(asm_lines)
        instr_offsets = [asm_lines[i][0] for i in instr_line_indices]

        for pc_hex in pcs:
            pc_val = int(pc_hex, 16)
            file_offset = pc_val - PIE_BASE
            ctx = find_context(asm_lines, instr_line_indices, instr_offsets,
                               file_offset, args.context_lines)
            if ctx is not None:
                asm_cache[(kernel, pc_hex)] = {
                    "pc_offset": f"0x{file_offset:x}",
                    "asm_context": ctx,
                }

    print(f"  {len(asm_cache)} unique (kernel, PC) pairs with asm context",
          file=sys.stderr)

    # ── Phase 4: Build JSONL records ────────────────────────────────────
    print("Phase 4: Building JSONL records...", file=sys.stderr)

    train_records = []
    test_records = []
    skipped_no_asm = 0

    for key, sample in samples.items():
        kernel = key[0]
        pc_hex = key[-1]
        inp = sample["input"]
        label = sample["label"]

        if (kernel, pc_hex) not in asm_cache:
            skipped_no_asm += 1
            continue

        asm_info = asm_cache[(kernel, pc_hex)]

        record = {
            "binary": kernel,
            "input": inp,  # may be None when --merge-inputs is set
            "pc_runtime": pc_hex,
            "pc_offset": asm_info["pc_offset"],
            "asm_context": asm_info["asm_context"],
            "label": {
                "filter": label["filter"],
                "pf_sel": label["pf_sel"],
                "pf_degree": label["pf_degree"],
            },
            "_aux": {
                "amat_no": label["amat_no"],
                "amat_best": label["amat_best"],
                "best_config": label["best_config"],
                "vote_count": label["vote_count"],
                "total_traces": label["total_traces"],
            },
        }

        if kernel in TRAIN_KERNELS:
            train_records.append(record)
        else:
            test_records.append(record)

    # Sort by (binary, input, pc_offset) for reproducibility
    sort_key = lambda r: (r["binary"], r["input"] or "", r["pc_offset"])
    train_records.sort(key=sort_key)
    test_records.sort(key=sort_key)

    print(f"  Skipped {skipped_no_asm} samples without asm context",
          file=sys.stderr)

    # ── Phase 5: Write output ───────────────────────────────────────────
    train_path = output_dir / "train.jsonl"
    test_path = output_dir / "test.jsonl"

    with open(train_path, "w") as f:
        for r in train_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(test_path, "w") as f:
        for r in test_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ── Phase 6: Print stats ────────────────────────────────────────────
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Dataset written:", file=sys.stderr)
    print(f"  Train: {train_path} ({len(train_records)} records)", file=sys.stderr)
    print(f"  Test:  {test_path} ({len(test_records)} records)", file=sys.stderr)

    for name, records in [("Train", train_records), ("Test", test_records)]:
        if not records:
            continue
        print(f"\n{name} stats:", file=sys.stderr)

        # Per-kernel counts
        kernel_counts = defaultdict(int)
        for r in records:
            kernel_counts[r["binary"]] += 1
        print(f"  Per kernel: {dict(kernel_counts)}", file=sys.stderr)

        # Filter distribution
        n_filter = sum(1 for r in records if r["label"]["filter"])
        print(f"  Filter=True: {n_filter}/{len(records)} "
              f"({100*n_filter/len(records):.1f}%)", file=sys.stderr)

        # PF selection distribution (non-filtered)
        pf_counts = defaultdict(int)
        degree_counts = defaultdict(int)
        for r in records:
            if not r["label"]["filter"]:
                pf_counts[r["label"]["pf_sel"]] += 1
                degree_counts[r["label"]["pf_degree"]] += 1
        print(f"  PF selection: {dict(pf_counts)}", file=sys.stderr)
        print(f"  PF degree: {dict(degree_counts)}", file=sys.stderr)

        # AMAT stats
        amat_nos = [r["_aux"]["amat_no"] for r in records]
        amat_bests = [r["_aux"]["amat_best"] for r in records]
        print(f"  AMAT(no):   min={min(amat_nos):.1f}, "
              f"median={sorted(amat_nos)[len(amat_nos)//2]:.1f}, "
              f"max={max(amat_nos):.1f}", file=sys.stderr)
        print(f"  AMAT(best): min={min(amat_bests):.1f}, "
              f"median={sorted(amat_bests)[len(amat_bests)//2]:.1f}, "
              f"max={max(amat_bests):.1f}", file=sys.stderr)

    print(f"\n{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
