#include "sms.h"

#include <algorithm>
#include <iostream>

#include "cache.h"

void sms::prefetcher_initialize() { pht.assign(PHT_SETS, {}); }

uint64_t sms::signature_of(champsim::address pc, uint32_t offset) const
{
  // Match Pythia: signature = (pc << log2(blocks_per_region)) | offset.
  // We use 64-bit unsigned to fit any pc; high bits of pc are dropped by shift.
  uint64_t pc_raw = pc.to<uint64_t>();
  return (pc_raw << 6) ^ static_cast<uint64_t>(offset);
}

void sms::pht_insert_or_update(uint64_t signature, const region_pattern& pattern)
{
  std::size_t set = signature & (PHT_SETS - 1);
  auto& bucket = pht[set];

  auto it = std::find_if(bucket.begin(), bucket.end(), [&](const pht_entry& e) { return e.valid && e.signature == signature; });

  if (it != bucket.end()) {
    it->pattern = pattern;
    it->age = 0;
    for (auto& e : bucket)
      if (&e != &(*it))
        ++e.age;
    stat_pht_hit++;
    return;
  }

  // Miss — insert (evict oldest if full).
  if (bucket.size() >= PHT_WAYS) {
    auto victim = std::max_element(bucket.begin(), bucket.end(), [](const pht_entry& a, const pht_entry& b) { return a.age < b.age; });
    bucket.erase(victim);
  }
  pht_entry ne{};
  ne.signature = signature;
  ne.pattern = pattern;
  ne.age = 0;
  ne.valid = true;
  for (auto& e : bucket)
    ++e.age;
  bucket.push_back(ne);
  stat_pht_inserts++;
}

void sms::evict_agt_to_pht(const agt_entry& entry)
{
  uint64_t sig = signature_of(entry.pc, entry.trigger_offset);
  pht_insert_or_update(sig, entry.pattern);
}

std::size_t sms::pht_lookup_and_queue(champsim::address pc, champsim::page_number page, uint32_t cur_offset)
{
  uint64_t sig = signature_of(pc, cur_offset);
  std::size_t set = sig & (PHT_SETS - 1);
  auto& bucket = pht[set];
  auto it = std::find_if(bucket.begin(), bucket.end(), [&](const pht_entry& e) { return e.valid && e.signature == sig; });

  if (it == bucket.end()) {
    stat_pht_miss++;
    return 0;
  }
  stat_pht_hit++;
  // Refresh LRU.
  it->age = 0;
  for (auto& e : bucket)
    if (&e != &(*it))
      ++e.age;

  std::size_t queued = 0;
  for (std::size_t i = 0; i < BLOCKS_PER_REGION; ++i) {
    if (queued >= PHT_REPLAY_CAP)
      break;
    if (it->pattern.test(i) && i != cur_offset) {
      // Build prefetch address: page << log2(page_size) | (i << log2(block_size))
      champsim::block_number page_first_block{champsim::address{page}};
      champsim::block_number target_block{page_first_block + static_cast<long>(i)};
      prefetch_queue.push_back(champsim::address{target_block});
      ++queued;
    }
  }
  stat_prefetch_generated += queued;
  return queued;
}

uint32_t sms::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t /*cache_hit*/, bool /*useful_prefetch*/, access_type /*type*/,
                                       uint32_t metadata_in)
{
  ++stat_called;
  champsim::page_number page{addr};
  champsim::block_number block{addr};
  champsim::block_number page_first_block{champsim::address{page}};
  uint32_t offset = static_cast<uint32_t>(champsim::offset(page_first_block, block));
  if (offset >= BLOCKS_PER_REGION) {
    return metadata_in; // shouldn't happen for normal addresses
  }

  // 1. AGT lookup
  auto agt_it = std::find_if(agt.begin(), agt.end(), [&](const agt_entry& e) { return e.page == page; });
  if (agt_it != agt.end()) {
    ++stat_agt_hit;
    agt_it->pattern.set(offset);
    agt_it->age = 0;
    for (auto& e : agt)
      if (&e != &(*agt_it))
        ++e.age;
    return metadata_in;
  }
  ++stat_agt_miss;

  // 2. FT lookup
  auto ft_it = std::find_if(filter_table.begin(), filter_table.end(), [&](const ft_entry& e) { return e.page == page; });
  if (ft_it != filter_table.end()) {
    // FT hit → promote to AGT, evict from FT
    ++stat_ft_hit;
    if (agt.size() >= AGT_SIZE) {
      auto victim = std::max_element(agt.begin(), agt.end(), [](const agt_entry& a, const agt_entry& b) { return a.age < b.age; });
      evict_agt_to_pht(*victim);
      agt.erase(victim);
    }
    agt_entry ne{};
    ne.page = ft_it->page;
    ne.pc = ft_it->pc;
    ne.trigger_offset = ft_it->trigger_offset;
    ne.pattern.set(ft_it->trigger_offset);
    ne.pattern.set(offset);
    ne.age = 0;
    for (auto& e : agt)
      ++e.age;
    agt.push_back(ne);
    filter_table.erase(ft_it);
    return metadata_in;
  }
  ++stat_ft_miss;

  // 3. FT miss → install in FT, look up PHT, queue prefetches
  if (filter_table.size() >= FT_SIZE) {
    // FIFO: oldest at front
    filter_table.pop_front();
  }
  ft_entry ne{};
  ne.page = page;
  ne.pc = ip;
  ne.trigger_offset = offset;
  filter_table.push_back(ne);

  pht_lookup_and_queue(ip, page, offset);

  return metadata_in;
}

void sms::prefetcher_cycle_operate()
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
  // Else keep at front and retry next cycle.
}

uint32_t sms::prefetcher_cache_fill(champsim::address /*addr*/, long /*set*/, long /*way*/, uint8_t /*prefetch*/, champsim::address /*evicted_addr*/,
                                    uint32_t metadata_in)
{
  return metadata_in;
}

void sms::prefetcher_final_stats()
{
  std::cout << "sms.called " << stat_called << "\n"
            << "sms.ft.hit " << stat_ft_hit << "\n"
            << "sms.ft.miss " << stat_ft_miss << "\n"
            << "sms.agt.hit " << stat_agt_hit << "\n"
            << "sms.agt.miss " << stat_agt_miss << "\n"
            << "sms.pht.hit " << stat_pht_hit << "\n"
            << "sms.pht.miss " << stat_pht_miss << "\n"
            << "sms.pht.inserts " << stat_pht_inserts << "\n"
            << "sms.prefetch.generated " << stat_prefetch_generated << "\n"
            << "sms.prefetch.issued " << stat_prefetch_issued << std::endl;
}
