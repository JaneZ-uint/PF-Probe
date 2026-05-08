# Pythia source — translation reference (NOT BUILT)

These files are vendored verbatim from CMU-SAFARI/Pythia (master branch,
fetched 2026/05/08) as a reference while porting Stream / SMS / Sandbox
prefetchers to modern ChampSim's `champsim::modules::prefetcher` API.

**This directory is not under `champsim/prefetcher/`, so ChampSim's config
auto-discovery (`config/modules.py`) will not pick it up.** Nothing here
gets compiled into the simulator.

## Files

| File | Source | Lines | Purpose |
|---|---|---|---|
| `streamer.{cc,h}` | `prefetcher/streamer.cc`, `inc/streamer.h` | 132 / 65 | Stream prefetcher (per-page direction tracker) |
| `sms.{cc,h}` | `prefetcher/sms.cc`, `inc/sms.h` | 381 / 148 | Spatial Memory Streaming (FT + AGT + PHT) |
| `sandbox.{cc,h}` | `prefetcher/sandbox.cc`, `inc/sandbox.h` | 346 / 94 | Sandbox prefetcher (Bloom + offset evaluation) |

## Source

- Repo: https://github.com/CMU-SAFARI/Pythia
- License: MIT
- Fetched: 2026/05/08

## API differences vs modern ChampSim

Pythia targets ChampSim 2.0:
- `uint64_t` raw addresses; manual `>> LOG2_PAGE_SIZE` etc.
- `invoke_prefetcher(pc, addr, hit, type, vector<uint64_t>& pref_addr)` — collects
  multiple prefetches per call into the output vector.
- Prefetchers inherit from a custom `Prefetcher` base, not ChampSim's modular base.
- "Knobs" are global externs configured at run time.

Modern ChampSim (commit 06de8d3, our target):
- Strongly-typed `champsim::address`, `champsim::block_number`, `champsim::page_number`.
- Inherits from `champsim::modules::prefetcher`; SFINAE-detected hooks
  (`prefetcher_cache_operate`, `prefetcher_cycle_operate`, ...).
- Issues prefetches one at a time via `intern_->prefetch_line(addr, fill_this_level, meta)`.
- Parameters baked into `static constexpr` members (no run-time knobs needed for our scope).

The translation pattern: collect prefetch addresses into a per-prefetcher
queue/lookahead during `prefetcher_cache_operate`; drain one per cycle in
`prefetcher_cycle_operate`. See `champsim/prefetcher/ip_stride/` for the
canonical lookahead pattern.
