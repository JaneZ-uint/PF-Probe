#!/usr/bin/env python3
"""
W3b Step 5 — Extract ±128 lines of assembly context around given PCs.

Usage:
  # From a file of hex PCs (one per line):
  python3 scripts/extract_asm_context.py vendor/gapbs/bfs --pcs pcs.txt -o out.json

  # From inline PCs:
  python3 scripts/extract_asm_context.py vendor/gapbs/bfs --pc 0x5555555596b6 --pc 0x555555559ae0

  # From a W3 grid JSON (auto-extracts PCs from per_pc_load_latency):
  python3 scripts/extract_asm_context.py vendor/gapbs/bfs --from-grid data/w3_grid/bfs_kron18_no.json -o out.json

PIE base is 0x555555550000 (setarch -R canonical). PCs outside the binary
range or at 0x0 are skipped.
"""

import argparse
import bisect
import json
import subprocess
import sys
from pathlib import Path

PIE_BASE = 0x555555550000


def parse_objdump(binary_path: str) -> list[tuple[int, str]]:
    """Run objdump and parse into [(file_offset, line_text), ...]."""
    result = subprocess.run(
        ["objdump", "-d", "--no-show-raw-insn", binary_path],
        capture_output=True, text=True, check=True,
    )
    lines = []
    for raw_line in result.stdout.splitlines():
        stripped = raw_line.strip()
        # Instruction lines: "   96b6:\tmov    0x80(%rsp),%rdi"
        if stripped and ":" in stripped:
            colon_idx = stripped.index(":")
            hex_part = stripped[:colon_idx].strip()
            try:
                offset = int(hex_part, 16)
                lines.append((offset, raw_line))
                continue
            except ValueError:
                pass
        # Non-instruction lines: function headers, section headers, blank lines
        # We keep them with offset = -1 so they appear in context
        lines.append((-1, raw_line))
    return lines


def build_offset_index(asm_lines: list[tuple[int, str]]) -> list[int]:
    """Build sorted list of (line_index) for lines that have valid offsets,
    with a parallel sorted offset list for binary search."""
    return [i for i, (off, _) in enumerate(asm_lines) if off >= 0]


def find_context(
    asm_lines: list[tuple[int, str]],
    instr_line_indices: list[int],
    instr_offsets: list[int],
    file_offset: int,
    context_lines: int = 128,
) -> str | None:
    """Find the instruction line closest to file_offset and return ±context_lines."""
    # Binary search: find the largest instruction offset <= file_offset
    pos = bisect.bisect_right(instr_offsets, file_offset)
    if pos == 0:
        return None
    # pos-1 is the index in instr_offsets of the largest offset <= file_offset
    line_idx = instr_line_indices[pos - 1]

    start = max(0, line_idx - context_lines)
    end = min(len(asm_lines), line_idx + context_lines + 1)

    context = []
    for i in range(start, end):
        _, text = asm_lines[i]
        if i == line_idx:
            context.append(f">>> {text}")  # mark the target line
        else:
            context.append(f"    {text}")
    return "\n".join(context)


def load_pcs_from_grid(json_path: str) -> list[str]:
    """Extract PC hex strings from a W3 grid JSON's per_pc_load_latency."""
    with open(json_path) as f:
        d = json.load(f)
    ppc = d[0]["roi"]["cpu0_L1D"]["per_pc_load_latency"]
    return list(ppc.keys())


def main():
    parser = argparse.ArgumentParser(description="Extract ±128 asm context around PCs")
    parser.add_argument("binary", help="Path to GAP binary (e.g. vendor/gapbs/bfs)")
    parser.add_argument("--pc", action="append", default=[], help="Runtime PC (hex, e.g. 0x5555555596b6)")
    parser.add_argument("--pcs", help="File with one hex PC per line")
    parser.add_argument("--from-grid", help="W3 grid JSON to extract PCs from")
    parser.add_argument("--context", type=int, default=128, help="Lines of context (default: 128)")
    parser.add_argument("--min-count", type=int, default=0,
                        help="When using --from-grid, only include PCs with count >= this")
    parser.add_argument("-o", "--output", help="Output JSON path (default: stdout)")
    args = parser.parse_args()

    # Collect PCs
    pc_hexes = list(args.pc)
    if args.pcs:
        with open(args.pcs) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    pc_hexes.append(line)
    if args.from_grid:
        if args.min_count > 0:
            with open(args.from_grid) as f:
                d = json.load(f)
            ppc = d[0]["roi"]["cpu0_L1D"]["per_pc_load_latency"]
            for pc_hex, v in ppc.items():
                if v["count"] >= args.min_count:
                    pc_hexes.append(pc_hex)
        else:
            pc_hexes.extend(load_pcs_from_grid(args.from_grid))

    if not pc_hexes:
        print("Error: no PCs specified. Use --pc, --pcs, or --from-grid.", file=sys.stderr)
        sys.exit(1)

    # Deduplicate while preserving order
    seen = set()
    unique_pcs = []
    for pc in pc_hexes:
        if pc not in seen:
            seen.add(pc)
            unique_pcs.append(pc)
    pc_hexes = unique_pcs

    print(f"Disassembling {args.binary}...", file=sys.stderr)
    asm_lines = parse_objdump(args.binary)
    print(f"  {len(asm_lines)} lines parsed", file=sys.stderr)

    # Build index: sorted instruction offsets + their line indices
    instr_line_indices = build_offset_index(asm_lines)
    instr_offsets = [asm_lines[i][0] for i in instr_line_indices]

    # Determine binary's address range from objdump
    valid_offsets = [off for off, _ in asm_lines if off >= 0]
    if not valid_offsets:
        print("Error: no instructions found in objdump output", file=sys.stderr)
        sys.exit(1)
    min_offset, max_offset = valid_offsets[0], valid_offsets[-1]

    results = {}
    skipped = {"null_pc": 0, "outside_binary": 0, "not_found": 0}

    for pc_hex in pc_hexes:
        pc_val = int(pc_hex, 16)

        # Skip null PC (page walk fills)
        if pc_val == 0:
            skipped["null_pc"] += 1
            continue

        # Convert runtime PC to file offset
        file_offset = pc_val - PIE_BASE

        # Skip PCs outside binary range (libc, vdso, etc.)
        if file_offset < min_offset or file_offset > max_offset + 0x100:
            skipped["outside_binary"] += 1
            continue

        ctx = find_context(asm_lines, instr_line_indices, instr_offsets,
                           file_offset, args.context)
        if ctx is None:
            skipped["not_found"] += 1
            continue

        results[pc_hex] = {
            "pc_offset": f"0x{file_offset:x}",
            "asm_context": ctx,
        }

    print(f"Results: {len(results)} PCs extracted, "
          f"skipped: {skipped}", file=sys.stderr)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        json.dump(results, sys.stdout, indent=2)
        print(file=sys.stdout)


if __name__ == "__main__":
    main()
