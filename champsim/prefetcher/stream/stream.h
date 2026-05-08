#ifndef PREFETCHER_STREAM_H
#define PREFETCHER_STREAM_H

#include <cstdint>
#include <optional>

#include "address.h"
#include "champsim.h"
#include "modules.h"
#include "msl/lru_table.h"

// Stream prefetcher ported from CMU-SAFARI/Pythia (prefetcher/streamer.cc).
// One stream tracker per 4 KB page; on two consecutive monotonic block
// accesses in the same direction within a page, queue PREFETCH_DEGREE
// line-stride prefetches in that direction, stopping at the page boundary.

struct stream : public champsim::modules::prefetcher {
  struct tracker_entry {
    champsim::page_number page{};
    champsim::block_number last_block{}; // most recent block_number observed in this page
    int32_t last_dir{0};                 // -1, 0, +1 (block-stride sign)
    uint8_t conf{0};                     // 1 once two same-direction accesses observed

    auto index() const { return page; }
    auto tag() const { return page; }
  };

  struct lookahead_entry {
    champsim::block_number next_block{};
    int32_t dir{0};       // +1 or -1
    int degree_remaining{0};
  };

  // Pythia default: 64 fully-associative trackers.
  constexpr static std::size_t TRACKER_SETS = 1;
  constexpr static std::size_t TRACKER_WAYS = 64;
  constexpr static int PREFETCH_DEGREE = 4;

  std::optional<lookahead_entry> active_lookahead;
  champsim::msl::lru_table<tracker_entry> table{TRACKER_SETS, TRACKER_WAYS};

  uint64_t stat_called{0};
  uint64_t stat_tracker_miss{0};
  uint64_t stat_tracker_hit{0};
  uint64_t stat_dir_match{0};
  uint64_t stat_prefetch_issued{0};

public:
  using champsim::modules::prefetcher::prefetcher;

  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
  void prefetcher_cycle_operate();
  void prefetcher_final_stats();
};

#endif
