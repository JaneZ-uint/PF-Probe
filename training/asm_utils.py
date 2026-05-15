"""
Shared utilities for PF-LLM training pipeline.

Used by:
  - training/convert_to_sharegpt.py (data preparation)
  - training/evaluate.py (inference + eval)
"""

import json
import re

# ── PF-LLM Listing 1 system prompt (exact) ──────────────────────────────
SYSTEM_PROMPT = (
    "You are a helpful assistant that generates prefetch hints for a given "
    "load instruction in a assembly code snippet.\n"
    "The load instruction is marked with <load> and </load>.\n"
    'Your output should be a JSON object with "PF Sel", '
    '"PF Degree" and "Filter" as fields.'
)

# Regex: matches objdump instruction lines like "   96b6:\tmov ..."
_INSTR_RE = re.compile(r"^[0-9a-f]+:\s*")
# Regex: matches function header lines like "0000000000096b0 <_ZN6CLBase...>:"
_FUNC_HEADER_RE = re.compile(r"^[0-9a-f]+ <(.+)>:$")


def asm_context_to_user_prompt(asm_context: str) -> str:
    """Convert raw objdump asm_context to the paper's user prompt format.

    Transformations:
    1. Strip ">>>" prefix → wrap that line with <load>...</load>
    2. Strip hex address prefix from instruction lines (e.g. "96b6:\t" → "")
    3. Keep function headers but strip leading address (just "<FuncName>:")
    4. Drop blank/whitespace-only lines
    5. Strip leading/trailing whitespace per line
    """
    out_lines = []
    for line in asm_context.split("\n"):
        # Detect target load instruction (marked with >>>)
        is_target = False
        stripped = line.strip()
        if stripped.startswith(">>>"):
            is_target = True
            stripped = stripped[3:].strip()

        # Skip empty lines
        if not stripped:
            continue

        # Try function header: "0000000000096b0 <_ZN6CLBase...>:"
        m = _FUNC_HEADER_RE.match(stripped)
        if m:
            out_lines.append(f"<{m.group(1)}>:")
            continue

        # Try instruction line: "96b6:\tmov 0x80(%rsp),%rdi"
        m = _INSTR_RE.match(stripped)
        if m:
            instr_text = stripped[m.end():]
            if is_target:
                out_lines.append(f"<load>{instr_text}</load>")
            else:
                out_lines.append(instr_text)
            continue

        # Section headers like "Disassembly of section .text:" — skip
        if stripped.startswith("Disassembly of") or stripped.endswith("file format"):
            continue

        # Other lines (rare) — keep as-is
        if is_target:
            out_lines.append(f"<load>{stripped}</load>")
        else:
            out_lines.append(stripped)

    return "\n".join(out_lines)


def label_to_response(label: dict) -> str:
    """Convert a label dict to the JSON response string (paper Listing 1 Lines 18-22).

    Input:  {"PF Sel": "sandbox", "PF Degree": 1, "Filter": "none"}
    Output: '{"PF Sel": "sandbox", "PF Degree": 1, "Filter": "none"}'
    """
    return json.dumps({
        "PF Sel": label["PF Sel"],
        "PF Degree": label["PF Degree"],
        "Filter": label["Filter"],
    })


def format_messages(user_prompt: str) -> list[dict]:
    """Build the chat messages list for tokenizer.apply_chat_template().

    Returns [{"role": "system", ...}, {"role": "user", ...}].
    The assistant response is NOT included (added during training or generation).
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
