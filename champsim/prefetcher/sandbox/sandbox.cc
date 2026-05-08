#include "sandbox.h"

#include <algorithm>
#include <cstdlib>
#include <functional>
#include <iostream>

#include "cache.h"

void sandbox::prefetcher_initialize()
{
  // Initial evaluated offsets: {+1..+8, -8..-1}
  for (int32_t o = 1; o <= 8; ++o)
    evaluated.push_back({o, 0});
  for (int32_t o = -8; o <= -1; ++o)
    evaluated.push_back({o, 0});
  // Reserve: {+9..+16, -16..-9}
  for (int32_t o = 9; o <= 16; ++o)
    non_evaluated.push_back(o);
  for (int32_t o = -16; o <= -9; ++o)
    non_evaluated.push_back(o);

  curr_ptr = 0;
  demand_in_phase = 0;
  bloom_clear();
}

std::size_t sandbox::bloom_hash(uint64_t v, uint32_t k) const
{
  uint64_t mixed = v ^ (static_cast<uint64_t>(k) * 0x9e3779b97f4a7c15ULL);
  uint64_t h = std::hash<uint64_t>{}(mixed);
  return h % BLOOM_BITS;
}

void sandbox::bloom_add(champsim::address addr)
{
  uint64_t v = addr.to<uint64_t>();
  for (uint32_t k = 0; k < BLOOM_HASHES; ++k)
    bloom.set(bloom_hash(v, k));
}

bool sandbox::bloom_lookup(champsim::address addr)
{
  uint64_t v = addr.to<uint64_t>();
  for (uint32_t k = 0; k < BLOOM_HASHES; ++k)
    if (!bloom.test(bloom_hash(v, k)))
      return false;
  return true;
}

void sandbox::bloom_clear() { bloom.reset(); }

std::optional<champsim::address> sandbox::generate_pf_addr(champsim::page_number page, uint32_t cur_offset, int32_t delta) const
{
  int32_t pref_offset = static_cast<int32_t>(cur_offset) + delta;
  if (pref_offset < 0 || static_cast<uint32_t>(pref_offset) >= BLOCKS_PER_PAGE)
    return std::nullopt;
  champsim::block_number page_first{champsim::address{page}};
  champsim::block_number target{page_first + static_cast<long>(pref_offset)};
  return champsim::address{target};
}

void sandbox::end_of_round()
{
  // Sort evaluated by score (descending).
  std::sort(evaluated.begin(), evaluated.end(), [](const score_entry& a, const score_entry& b) { return a.score > b.score; });

  // Cycle out NUM_CYCLE_OFFSETS lowest scoring → push their offsets back to reserve.
  for (uint32_t i = 0; i < NUM_CYCLE_OFFSETS && !evaluated.empty(); ++i) {
    int32_t off = evaluated.back().offset;
    evaluated.pop_back();
    non_evaluated.push_back(off);
  }
  // Cycle in NUM_CYCLE_OFFSETS fresh from reserve.
  for (uint32_t i = 0; i < NUM_CYCLE_OFFSETS && !non_evaluated.empty(); ++i) {
    int32_t off = non_evaluated.front();
    non_evaluated.pop_front();
    evaluated.push_back({off, 0});
  }
}

uint32_t sandbox::prefetcher_cache_operate(champsim::address addr, champsim::address /*ip*/, uint8_t /*cache_hit*/, bool /*useful_prefetch*/,
                                           access_type /*type*/, uint32_t metadata_in)
{
  ++stat_called;

  champsim::page_number page{addr};
  champsim::block_number block{addr};
  champsim::block_number page_first_block{champsim::address{page}};
  long block_offset_long = champsim::offset(page_first_block, block);
  if (block_offset_long < 0 || block_offset_long >= static_cast<long>(BLOCKS_PER_PAGE))
    return metadata_in;
  uint32_t cur_offset = static_cast<uint32_t>(block_offset_long);

  // Step 1: Bloom lookup → on hit, score++ for active offset.
  ++demand_in_phase;
  if (bloom_lookup(addr)) {
    ++stat_filter_hit;
    if (curr_ptr < evaluated.size())
      ++evaluated[curr_ptr].score;
  }

  // Step 2: Pseudo-prefetch using active offset → add to Bloom.
  if (curr_ptr < evaluated.size()) {
    int32_t active_off = evaluated[curr_ptr].offset;
    auto pseudo = generate_pf_addr(page, cur_offset, active_off);
    if (pseudo.has_value()) {
      bloom_add(*pseudo);
      ++stat_filter_add;
    }
  }

  // Step 3: phase / round end?
  if (demand_in_phase >= PHASE_LENGTH) {
    ++stat_end_of_phase;
    bloom_clear();
    demand_in_phase = 0;
    uint32_t next_ptr = curr_ptr + 1;
    if (next_ptr >= evaluated.size()) {
      // End of round: rotate offsets.
      ++stat_end_of_round;
      end_of_round();
      curr_ptr = 0;
    } else {
      curr_ptr = next_ptr;
    }
  }

  // Step 4: real prefetches based on current scores. Sort by absolute offset
  // ascending — Sandbox prefers small strides (Pugsley 2014).
  std::vector<score_entry> sorted_pos, sorted_neg;
  sorted_pos.reserve(8);
  sorted_neg.reserve(8);
  for (const auto& e : evaluated) {
    if (e.offset > 0)
      sorted_pos.push_back(e);
    else
      sorted_neg.push_back(e);
  }
  auto by_abs = [](const score_entry& a, const score_entry& b) { return std::abs(a.offset) < std::abs(b.offset); };
  std::sort(sorted_pos.begin(), sorted_pos.end(), by_abs);
  std::sort(sorted_neg.begin(), sorted_neg.end(), by_abs);

  uint32_t pos_count = 0;
  for (const auto& e : sorted_pos) {
    if (e.score >= PHASE_LENGTH) {
      auto pf = generate_pf_addr(page, cur_offset, e.offset);
      if (pf.has_value()) {
        prefetch_queue.push_back(*pf);
        ++stat_prefetch_generated;
        if (++pos_count > PREF_DEGREE)
          break;
      }
    }
  }
  uint32_t neg_count = 0;
  for (const auto& e : sorted_neg) {
    if (e.score >= PHASE_LENGTH) {
      auto pf = generate_pf_addr(page, cur_offset, e.offset);
      if (pf.has_value()) {
        prefetch_queue.push_back(*pf);
        ++stat_prefetch_generated;
        if (++neg_count > PREF_DEGREE)
          break;
      }
    }
  }

  return metadata_in;
}

void sandbox::prefetcher_cycle_operate()
{
  if (prefetch_queue.empty())
    return;
  champsim::address pf_addr = prefetch_queue.front();
  bool fill_this_level = intern_->get_mshr_occupancy_ratio() < 0.5;
  bool ok = prefetch_line(pf_addr, fill_this_level, 0);
  if (ok) {
    prefetch_queue.pop_front();
    ++stat_prefetch_issued;
  }
}

uint32_t sandbox::prefetcher_cache_fill(champsim::address /*addr*/, long /*set*/, long /*way*/, uint8_t /*prefetch*/, champsim::address /*evicted_addr*/,
                                        uint32_t metadata_in)
{
  return metadata_in;
}

void sandbox::prefetcher_final_stats()
{
  std::cout << "sandbox.called " << stat_called << "\n"
            << "sandbox.filter_hit " << stat_filter_hit << "\n"
            << "sandbox.filter_add " << stat_filter_add << "\n"
            << "sandbox.end_of_phase " << stat_end_of_phase << "\n"
            << "sandbox.end_of_round " << stat_end_of_round << "\n"
            << "sandbox.prefetch_generated " << stat_prefetch_generated << "\n"
            << "sandbox.prefetch_issued " << stat_prefetch_issued << "\n";

  // Top-scoring evaluated offsets at end of run — quick sanity check.
  auto sorted = evaluated;
  std::sort(sorted.begin(), sorted.end(), [](const score_entry& a, const score_entry& b) { return a.score > b.score; });
  std::cout << "sandbox.top_offsets ";
  for (std::size_t i = 0; i < std::min<std::size_t>(5, sorted.size()); ++i) {
    std::cout << sorted[i].offset << ":" << sorted[i].score << " ";
  }
  std::cout << std::endl;
}
