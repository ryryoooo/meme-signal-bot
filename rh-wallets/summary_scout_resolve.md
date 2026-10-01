# Scout TG trunc resolve summary

- Updated (UTC): 2026-10-01T02:00:23.906365+00:00
- Unique pairs: **2778/3243** (85.7%)
- Trunc rows still unresolved: **1570**
- Methods: pair_cache=3025 token_scoped=7 multi_union=1 mega=214 global=30 two_hit=0
- Blockscout: tokens_fetched=1 nonempty=1 api_fail=0 gmgn_calls=0
- Unresolved reasons (unique pairs): no_token_pool=0 api_fail=138 multi_match=0 no_match=327

## Top unresolved blockers

- `no_match`: 327
- `api_fail`: 138


## Hard ceiling / plateau

- no_match total: **327** (huge_pool≥1000 true-dead≈64, mid<500 still deepenable≈211)
- neighbor_ca hits this run: **0**
- Cross-pool mega-union miss on all current no_match ⇒ trunc never appears in any cached BS/GT pool.
- Ceiling: keep chunked BS redeepen for mid pools; huge_pool no_match needs new TG CA association or alternate indexer — do not burn GMGN traders here.

### Sample unresolved (up to 40)

- `0x0000|beef` reason=no_match tokens=1 union=147 tier=good
- `0x002a|84eb` reason=no_match tokens=1 union=194 tier=good
- `0x003c|7889` reason=api_fail tokens=1 union=0 tier=elite
- `0x008b|eab5` reason=api_fail tokens=1 union=0 tier=good
- `0x023b|f6d8` reason=no_match tokens=11 union=456 tier=good
- `0x02d4|6ae9` reason=no_match tokens=7 union=3919 tier=elite
- `0x06df|21c8` reason=no_match tokens=4 union=134 tier=elite
- `0x0749|93c1` reason=no_match tokens=5 union=643 tier=good
- `0x0763|e416` reason=api_fail tokens=4 union=0 tier=elite
- `0x0765|b870` reason=no_match tokens=3 union=343 tier=good
- `0x0768|1a43` reason=no_match tokens=1 union=145 tier=good
- `0x08ce|c7f4` reason=api_fail tokens=4 union=0 tier=elite
- `0x091e|dbfa` reason=no_match tokens=1 union=135 tier=elite
- `0x09e8|5769` reason=no_match tokens=1 union=148 tier=good
- `0x0a48|b42c` reason=no_match tokens=12 union=1618 tier=elite
- `0x0ae2|b485` reason=no_match tokens=8 union=134 tier=good
- `0x0d1d|4447` reason=no_match tokens=13 union=1359 tier=elite
- `0x0e2a|1708` reason=api_fail tokens=1 union=0 tier=good
- `0x0f60|def6` reason=no_match tokens=3 union=272 tier=good
- `0x0f72|4d3f` reason=no_match tokens=1 union=149 tier=good
- `0x0f86|98de` reason=no_match tokens=5 union=608 tier=good
- `0x0f9e|61e0` reason=no_match tokens=8 union=236 tier=elite
- `0x0fab|2bc6` reason=api_fail tokens=4 union=0 tier=good
- `0x1002|b2f5` reason=no_match tokens=4 union=3919 tier=elite
- `0x103f|7953` reason=api_fail tokens=1 union=0 tier=elite
- `0x104b|bbd6` reason=api_fail tokens=1 union=0 tier=good
- `0x1086|f10e` reason=no_match tokens=7 union=402 tier=elite
- `0x10cb|e21c` reason=api_fail tokens=4 union=0 tier=good
- `0x113f|ac20` reason=no_match tokens=1 union=248 tier=elite
- `0x12c5|3470` reason=no_match tokens=1 union=311 tier=elite
- `0x12fd|6a34` reason=api_fail tokens=3 union=0 tier=good
- `0x1343|8a62` reason=no_match tokens=1 union=138 tier=elite
- `0x137c|7773` reason=api_fail tokens=1 union=0 tier=elite
- `0x13e7|d7cd` reason=api_fail tokens=5 union=0 tier=elite
- `0x1435|b0ae` reason=no_match tokens=1 union=536 tier=good
- `0x145b|3ff6` reason=no_match tokens=7 union=192 tier=elite
- `0x150c|82a5` reason=no_match tokens=1 union=154 tier=good
- `0x15b0|5ea4` reason=api_fail tokens=1 union=0 tier=elite
- `0x15ba|2d77` reason=no_match tokens=3 union=337 tier=elite
- `0x164d|8943` reason=api_fail tokens=7 union=0 tier=elite
