#include "stream.h"

#include <iostream>

#include "cache.h"

uint32_t stream::prefetcher_cache_operate(champsim::address addr, champsim::address /*ip*/, uint8_t /*cache_hit*/, bool /*useful_prefetch*/,
                                          access_type /*type*/, uint32_t metadata_in)
{
  ++stat_called;

  champsim::page_number cur_page{addr};
  champsim::block_number cur_block{addr};

  // Probe tracker: try to find an existing stream for this page.
  tracker_entry probe{};
  probe.page = cur_page;
  auto found = table.check_hit(probe);

  if (!found.has_value()) {
    // First time we see this page: install a tracker and bail without
    // prefetching (need a second access to know the direction).
    ++stat_tracker_miss;
    tracker_entry fresh{};
    fresh.page = cur_page;
    fresh.last_block = cur_block;
    fresh.last_dir = 0;
    fresh.conf = 0;
    table.fill(fresh);
    return metadata_in;
  }

  ++stat_tracker_hit;

  // Same cacheline as last touch: nothing to learn.
  auto stride = champsim::offset(found->last_block, cur_block);
  if (stride == 0) {
    return metadata_in;
  }

  int32_t dir = (stride > 0) ? +1 : -1;
  bool dir_match = (dir == found->last_dir);

  tracker_entry updated = *found;
  updated.last_block = cur_block;
  updated.last_dir = dir;
  updated.conf = dir_match ? 1 : 0;
  table.fill(updated);

  if (dir_match) {
    ++stat_dir_match;
    // Queue degree prefetches in `dir` starting from the cacheline after `cur_block`.
    active_lookahead = lookahead_entry{cur_block, dir, PREFETCH_DEGREE};
  }

  return metadata_in;
}

void stream::prefetcher_cycle_operate()
{
  if (!active_lookahead.has_value()) {
    return;
  }

  auto& la = active_lookahead.value();
  if (la.degree_remaining <= 0) {
    active_lookahead.reset();
    return;
  }

  champsim::block_number next{la.next_block + la.dir};
  champsim::address pf_address{next};

  // Stop at page boundary unless the cache uses virtual prefetch addresses.
  if (!intern_->virtual_prefetch && champsim::page_number{pf_address} != champsim::page_number{champsim::address{la.next_block}}) {
    active_lookahead.reset();
    return;
  }

  bool fill_this_level = intern_->get_mshr_occupancy_ratio() < 0.5;
  bool ok = prefetch_line(pf_address, fill_this_level, 0);
  if (ok) {
    ++stat_prefetch_issued;
    la.next_block = next;
    la.degree_remaining -= 1;
    if (la.degree_remaining == 0) {
      active_lookahead.reset();
    }
  }
  // Else: keep the lookahead and retry next cycle.
}

uint32_t stream::prefetcher_cache_fill(champsim::address /*addr*/, long /*set*/, long /*way*/, uint8_t /*prefetch*/, champsim::address /*evicted_addr*/,
                                       uint32_t metadata_in)
{
  return metadata_in;
}

void stream::prefetcher_final_stats()
{
  std::cout << "stream.called " << stat_called << "\n"
            << "stream.tracker.miss " << stat_tracker_miss << "\n"
            << "stream.tracker.hit " << stat_tracker_hit << "\n"
            << "stream.dir_match " << stat_dir_match << "\n"
            << "stream.prefetch_issued " << stat_prefetch_issued << std::endl;
}
