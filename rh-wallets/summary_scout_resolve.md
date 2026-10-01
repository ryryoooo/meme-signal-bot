# Scout TG trunc resolve summary

- Updated (UTC): 2026-10-01T08:00:14.697815+00:00
- Unique pairs: **2901/3257** (89.1%)
- Trunc rows still unresolved: **864**
- Methods: pair_cache=665 token_scoped=24 multi_union=0 mega=87 global=12 two_hit=0
- Blockscout: tokens_fetched=4 nonempty=4 api_fail=0 gmgn_calls=0
- Unresolved reasons (unique pairs): no_token_pool=0 api_fail=94 multi_match=0 no_match=262

## Top unresolved blockers

- `no_match`: 262
- `api_fail`: 94


## Hard ceiling / plateau

- no_match total: **262** (huge_pool≥1000 true-dead≈46, mid<500 still deepenable≈175)
- neighbor_ca hits this run: **0**
- Cross-pool mega-union miss on all current no_match ⇒ trunc never appears in any cached BS/GT pool.
- Ceiling: keep chunked BS redeepen for mid pools; huge_pool no_match needs new TG CA association or alternate indexer — do not burn GMGN traders here.

### Sample unresolved (up to 40)

- `0x0000|beef` reason=no_match tokens=1 union=147 tier=good
- `0x002a|84eb` reason=no_match tokens=1 union=194 tier=good
- `0x008b|eab5` reason=api_fail tokens=1 union=0 tier=good
- `0x0749|93c1` reason=no_match tokens=5 union=643 tier=good
- `0x0765|b870` reason=no_match tokens=3 union=343 tier=good
- `0x0768|1a43` reason=no_match tokens=1 union=145 tier=good
- `0x080d|8ea9` reason=api_fail tokens=1 union=0 tier=elite
- `0x08f3|acd5` reason=api_fail tokens=1 union=0 tier=elite
- `0x091e|dbfa` reason=no_match tokens=1 union=135 tier=elite
- `0x09e8|5769` reason=no_match tokens=1 union=148 tier=good
- `0x0e2a|1708` reason=api_fail tokens=1 union=0 tier=good
- `0x0f60|def6` reason=no_match tokens=3 union=272 tier=good
- `0x0f72|4d3f` reason=no_match tokens=1 union=149 tier=good
- `0x0f86|98de` reason=no_match tokens=5 union=608 tier=good
- `0x0fab|2bc6` reason=api_fail tokens=4 union=0 tier=good
- `0x1002|b2f5` reason=no_match tokens=4 union=4118 tier=elite
- `0x1086|f10e` reason=no_match tokens=8 union=402 tier=elite
- `0x10cb|e21c` reason=api_fail tokens=4 union=0 tier=good
- `0x113f|ac20` reason=no_match tokens=1 union=248 tier=elite
- `0x12c5|3470` reason=no_match tokens=1 union=311 tier=elite
- `0x12fd|6a34` reason=api_fail tokens=3 union=0 tier=good
- `0x1343|8a62` reason=no_match tokens=1 union=138 tier=elite
- `0x1435|b0ae` reason=no_match tokens=1 union=536 tier=good
- `0x150c|82a5` reason=no_match tokens=1 union=154 tier=good
- `0x15b0|5ea4` reason=api_fail tokens=1 union=0 tier=elite
- `0x15ba|2d77` reason=no_match tokens=3 union=337 tier=elite
- `0x164d|8943` reason=api_fail tokens=7 union=0 tier=elite
- `0x1809|845d` reason=api_fail tokens=1 union=0 tier=good
- `0x1956|e514` reason=api_fail tokens=1 union=0 tier=elite
- `0x19e5|5af9` reason=no_match tokens=1 union=128 tier=good
- `0x1a63|5812` reason=no_match tokens=1 union=335 tier=good
- `0x1aa7|0615` reason=api_fail tokens=6 union=0 tier=elite
- `0x1aef|ce1a` reason=no_match tokens=1 union=929 tier=elite
- `0x1b14|8269` reason=no_match tokens=1 union=156 tier=elite
- `0x1b4c|cb05` reason=no_match tokens=2 union=358 tier=good
- `0x1b58|b8c0` reason=no_match tokens=1 union=133 tier=good
- `0x1b7c|647f` reason=api_fail tokens=1 union=0 tier=elite
- `0x1cd7|9573` reason=no_match tokens=1 union=155 tier=good
- `0x1d99|0161` reason=no_match tokens=4 union=1484 tier=elite
- `0x1db8|28fa` reason=api_fail tokens=2 union=0 tier=elite
