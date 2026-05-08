#ifndef PREFETCHER_SMS_H
#define PREFETCHER_SMS_H

#include <bitset>
#include <cstdint>
#include <deque>
#include <vector>

#include "address.h"
#include "champsim.h"
#include "modules.h"

// Spatial Memory Streaming prefetcher (Somogyi et al. 2006), ported from
// CMU-SAFARI/Pythia (prefetcher/sms.cc).
//
// Region granularity: we use the ChampSim page (4 KB) as the region — gives us
// `champsim::page_number` for free instead of defining a custom slice. The
// classic SMS paper uses 2 KB regions; effect is similar at 4 KB. 64 cachelines
// per region.
//
// Structure:
//   FT  — Filter Table: tracks pages on first access; entry leaves on second hit.
//   AGT — Active Generation Table: accumulates the access bitmap during the
//         page's "generation". Spills to PHT on LRU eviction.
//   PHT — Pattern History Table: indexed by (PC, trigger_offset) signature;
//         stores a 64-bit access bitmap that gets replayed on signature hit.
//
// On a demand access:
//   1. AGT hit  → set bitmap bit, refresh LRU
//   2. AGT miss → FT hit  → promote to AGT (start a new generation)
//                 FT miss → install in FT; look up PHT by (PC, offset);
//                          if PHT hit, queue all set bits as prefetch addresses

struct sms : public champsim::modules::prefetcher {
  static constexpr std::size_t BLOCKS_PER_REGION = 64; // 4 KB / 64 B
  using region_pattern = std::bitset<BLOCKS_PER_REGION>;

  static constexpr std::size_t FT_SIZE = 32;
  static constexpr std::size_t AGT_SIZE = 32;
  static constexpr std::size_t PHT_SETS = 1024;
  static constexpr std::size_t PHT_WAYS = 16; // 16K entries total

  struct ft_entry {
    champsim::page_number page{};
    champsim::address pc{};
    uint32_t trigger_offset{0};
    uint64_t age{0}; // higher = older
  };

  struct agt_entry {
    champsim::page_number page{};
    champsim::address pc{};
    uint32_t trigger_offset{0};
    region_pattern pattern{};
    uint64_t age{0};
  };

  struct pht_entry {
    uint64_t signature{0};
    region_pattern pattern{};
    uint64_t age{0};
    bool valid{false};
  };

  std::deque<ft_entry> filter_table;
  std::deque<agt_entry> agt;
  std::vector<std::deque<pht_entry>> pht; // pht[set]
  std::deque<champsim::address> prefetch_queue;

  uint64_t stat_called{0};
  uint64_t stat_ft_hit{0}, stat_ft_miss{0};
  uint64_t stat_agt_hit{0}, stat_agt_miss{0};
  uint64_t stat_pht_hit{0}, stat_pht_miss{0};
  uint64_t stat_pht_inserts{0};
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
  uint64_t signature_of(champsim::address pc, uint32_t offset) const;
  void evict_agt_to_pht(const agt_entry& entry);
  void pht_insert_or_update(uint64_t signature, const region_pattern& pattern);
  // Returns count of prefetches queued.
  std::size_t pht_lookup_and_queue(champsim::address pc, champsim::page_number page, uint32_t cur_offset);
};

#endif
