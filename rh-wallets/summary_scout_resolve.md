# Scout TG trunc resolve summary

- Updated (UTC): 2026-09-20T02:00:13.051824+00:00
- Unique pairs: **2127/2451** (86.8%)
- Trunc rows still unresolved: **512**
- Methods: pair_cache=2 token_scoped=0 multi_union=0 mega=2 global=0 two_hit=0
- Blockscout: tokens_fetched=1 nonempty=1 api_fail=0 gmgn_calls=0
- Unresolved reasons (unique pairs): no_token_pool=0 api_fail=0 multi_match=0 no_match=324

## Top unresolved blockers

- `no_match`: 324


## Hard ceiling / plateau

- no_match total: **324** (huge_pool≥1000 true-dead≈29, mid<500 still deepenable≈271)
- neighbor_ca hits this run: **0**
- Cross-pool mega-union miss on all current no_match ⇒ trunc never appears in any cached BS/GT pool.
- Ceiling: keep chunked BS redeepen for mid pools; huge_pool no_match needs new TG CA association or alternate indexer — do not burn GMGN traders here.

### Sample unresolved (up to 40)

- `0x0000|beef` reason=no_match tokens=1 union=147 tier=good
- `0x0000|ffa1` reason=no_match tokens=1 union=154 tier=good
- `0x002a|84eb` reason=no_match tokens=1 union=194 tier=good
- `0x004a|df4f` reason=no_match tokens=1 union=270 tier=good
- `0x005b|4b10` reason=no_match tokens=1 union=151 tier=good
- `0x005c|1099` reason=no_match tokens=1 union=148 tier=good
- `0x00d1|acc8` reason=no_match tokens=1 union=249 tier=good
- `0x0132|80c3` reason=no_match tokens=2 union=770 tier=good
- `0x03ba|8b3a` reason=no_match tokens=2 union=277 tier=good
- `0x069a|1033` reason=no_match tokens=1 union=122 tier=good
- `0x0749|93c1` reason=no_match tokens=2 union=289 tier=good
- `0x0768|1a43` reason=no_match tokens=1 union=145 tier=good
- `0x091e|dbfa` reason=no_match tokens=1 union=135 tier=elite
- `0x09e8|5769` reason=no_match tokens=1 union=148 tier=good
- `0x0e5a|8b75` reason=no_match tokens=1 union=168 tier=good
- `0x0edf|12ce` reason=no_match tokens=1 union=190 tier=good
- `0x0f60|def6` reason=no_match tokens=2 union=272 tier=good
- `0x0f72|4d3f` reason=no_match tokens=1 union=149 tier=good
- `0x0f86|98de` reason=no_match tokens=4 union=501 tier=good
- `0x0fb5|1b94` reason=no_match tokens=1 union=122 tier=elite
- `0x1002|b2f5` reason=no_match tokens=2 union=3347 tier=elite
- `0x110a|acfe` reason=no_match tokens=1 union=145 tier=good
- `0x113f|ac20` reason=no_match tokens=1 union=248 tier=elite
- `0x12c5|3470` reason=no_match tokens=1 union=311 tier=elite
- `0x1343|8a62` reason=no_match tokens=1 union=138 tier=elite
- `0x1364|0f7b` reason=no_match tokens=1 union=135 tier=good
- `0x140f|8ffc` reason=no_match tokens=1 union=139 tier=elite
- `0x1435|b0ae` reason=no_match tokens=1 union=127 tier=good
- `0x14f1|a520` reason=no_match tokens=2 union=240 tier=elite
- `0x150c|82a5` reason=no_match tokens=1 union=154 tier=good
- `0x1613|db45` reason=no_match tokens=1 union=230 tier=good
- `0x1841|fe32` reason=no_match tokens=2 union=251 tier=elite
- `0x19e5|5af9` reason=no_match tokens=1 union=128 tier=good
- `0x19ee|51ad` reason=no_match tokens=2 union=258 tier=good
- `0x1a63|5812` reason=no_match tokens=1 union=335 tier=good
- `0x1aef|ce1a` reason=no_match tokens=1 union=929 tier=elite
- `0x1b14|8269` reason=no_match tokens=1 union=156 tier=elite
- `0x1b4c|cb05` reason=no_match tokens=2 union=358 tier=good
- `0x1b58|b8c0` reason=no_match tokens=1 union=133 tier=good
- `0x1cd7|9573` reason=no_match tokens=1 union=155 tier=good
