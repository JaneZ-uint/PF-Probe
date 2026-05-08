#ifndef PREFETCHER_SANDBOX_H
#define PREFETCHER_SANDBOX_H

#include <bitset>
#include <cstdint>
#include <deque>
#include <optional>

#include "address.h"
#include "champsim.h"
#include "modules.h"

// Sandbox prefetcher (Pugsley et al., HPCA 2014), ported from
// CMU-SAFARI/Pythia (prefetcher/sandbox.cc).
//
// Maintains 16 candidate cacheline-stride offsets being "evaluated" at any
// time, plus 16 in reserve. Each phase (256 demand accesses) is dedicated to
// one offset: the prefetcher inserts pseudo-prefetch addresses (current addr +
// active_offset * 64 B) into a Bloom filter, then on subsequent demands
// checks whether the demand address is already in the Bloom. A hit means the
// offset would have prefetched a useful line — increment that offset's score.
// At end-of-round (after all 16 offsets have run a phase), the lowest scoring
// offsets are swapped out for fresh ones from the reserve.
//
// Real prefetches are issued continuously: on every demand access, any
// evaluated offset whose accumulated score crosses a threshold fires a real
// prefetch (current addr + that offset * 64 B). Score threshold = phase length.

struct sandbox : public champsim::modules::prefetcher {
  static constexpr std::size_t BLOOM_BITS = 2048;
  static constexpr std::size_t BLOOM_HASHES = 2;
  static constexpr uint32_t PHASE_LENGTH = 256;
  static constexpr uint32_t NUM_CYCLE_OFFSETS = 4;
  static constexpr uint32_t PREF_DEGREE = 4; // per direction
  static constexpr uint32_t BLOCKS_PER_PAGE = 64;
  static constexpr std::size_t NUM_EVALUATED = 16;

  struct score_entry {
    int32_t offset{0};
    uint32_t score{0};
  };

  std::bitset<BLOOM_BITS> bloom;

  std::deque<score_entry> evaluated;     // exactly NUM_EVALUATED entries
  std::deque<int32_t> non_evaluated;     // reserve pool

  uint32_t curr_ptr{0};
  uint32_t demand_in_phase{0};

  std::deque<champsim::address> prefetch_queue;

  uint64_t stat_called{0};
  uint64_t stat_filter_hit{0};
  uint64_t stat_filter_add{0};
  uint64_t stat_end_of_phase{0};
  uint64_t stat_end_of_round{0};
  uint64_t stat_prefetch_generated{0};
  uint64_t stat_prefetch_issued{0};

public:
  using champsim::modules::prefetcher::prefetcher;

  void prefetcher_initialize();
  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
  void prefetcher_cycle_operate();
  void prefetcher_final_stats();

private:
  std::size_t bloom_hash(uint64_t v, uint32_t k) const;
  void bloom_add(champsim::address addr);
  bool bloom_lookup(champsim::address addr);
  void bloom_clear();
  std::optional<champsim::address> generate_pf_addr(champsim::page_number page, uint32_t cur_offset, int32_t delta) const;
  void end_of_round();
};

#endif
