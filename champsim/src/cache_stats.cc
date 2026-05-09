#include "cache_stats.h"

cache_stats operator-(cache_stats lhs, cache_stats rhs)
{
  cache_stats result;
  result.pf_requested = lhs.pf_requested - rhs.pf_requested;
  result.pf_issued = lhs.pf_issued - rhs.pf_issued;
  result.pf_useful = lhs.pf_useful - rhs.pf_useful;
  result.pf_useless = lhs.pf_useless - rhs.pf_useless;
  result.pf_fill = lhs.pf_fill - rhs.pf_fill;

  result.hits = lhs.hits - rhs.hits;
  result.misses = lhs.misses - rhs.misses;

  result.total_miss_latency_cycles = lhs.total_miss_latency_cycles - rhs.total_miss_latency_cycles;

  // Per-PC AMAT delta = lhs - rhs over union of keys.
  result.per_pc_load_latency = lhs.per_pc_load_latency;
  for (const auto& [pc, rv] : rhs.per_pc_load_latency) {
    auto& cur = result.per_pc_load_latency[pc];
    cur.first -= rv.first;
    cur.second -= rv.second;
  }
  return result;
}
