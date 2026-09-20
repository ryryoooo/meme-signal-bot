# RH on-chain hot active wallets

- Updated: **2026-09-20 18:29 JST**
- Window: blocks `67828507` … `67829306` (**800** scanned, tip=67829306)
- RPC calls: **1667** (429s=281)
- Elapsed: **312.8s**
- Total txs in window: **5122** · unique senders: **2072**
- Candidates (≥2 txs): **793** · receipt-checked: **120** (receipts=866)
- Hot wallets: **151** (spam bots dropped: 1)
- Known-good CAs loaded: **1139** · watch overlap set: **2772**
- In-watch among hot: **3** · new discoveries: **148**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0x1e9cec1a621ea64eda3db12d4a844124f30eb6f0` | 116.0 | 13 | 5 | 6 | 0 |  | 67829247 |
| 2 | `0x8b216cc896f32bcabca3649ca918d532f72010ef` | 104.0 | 10 | 5 | 6 | 0 |  | 67829264 |
| 3 | `0xd657bf12b59dfde3573c521abc3b111b62dfe3c5` | 98.0 | 9 | 4 | 4 | 0 | Y | 67829242 |
| 4 | `0x062280e98e31b64c3ab06a1de88d1525a9420629` | 86.0 | 10 | 4 | 4 | 0 |  | 67828865 |
| 5 | `0x55454b47051b2b418600dd76f81d97d475d8b988` | 92.0 | 12 | 3 | 6 | 0 |  | 67829051 |
| 6 | `0xd19750949328a577a0eb8157bf6defd82ee302b4` | 80.0 | 10 | 3 | 4 | 0 |  | 67828825 |
| 7 | `0x2eb8d214fa31ee2a523a4904d18e286540e7cf2f` | 63.0 | 9 | 3 | 3 | 0 |  | 67829033 |
| 8 | `0xc3d5ca2040fa83905085c886e24f07d3569f4a33` | 73.0 | 9 | 2 | 7 | 0 |  | 67829269 |
| 9 | `0x9f2f29f7a40a133c7abddc2a82715baa429546c6` | 94.0 | 20 | 2 | 4 | 0 |  | 67829087 |
| 10 | `0x141b4102b22457d397ebeb69fa6ea052f604c9b1` | 54.0 | 6 | 2 | 4 | 0 |  | 67829170 |
| 11 | `0x8bc8aecab84e3a5d64215b9a777d9f2b31063fc8` | 62.0 | 11 | 2 | 2 | 0 |  | 67828942 |
| 12 | `0x06362c455f2393f7a1608f022a45fa69d0e3efb4` | 48.0 | 7 | 2 | 2 | 0 |  | 67829174 |
| 13 | `0xadf3672e396aff6b8cd2bc2cda259ccfd7aa9fd5` | 44.0 | 5 | 2 | 2 | 0 |  | 67829184 |
| 14 | `0x1946996c01faab505281f29af769fbf381abee3a` | 44.0 | 5 | 2 | 2 | 0 |  | 67828697 |
| 15 | `0x8872aeb8074fac657466e5604452487f5de7bfac` | 44.0 | 5 | 2 | 2 | 0 |  | 67828692 |
| 16 | `0x7eb196384c5dfd9515841636fad826603f1b8e84` | 42.0 | 4 | 2 | 2 | 0 |  | 67829233 |
| 17 | `0xe909d960e1a8466fc65cf4925bc8e55cc235871b` | 40.0 | 4 | 2 | 2 | 0 |  | 67829183 |
| 18 | `0x943143311d9f3a0b580b3b578cc4a1a15d7285cd` | 37.0 | 5 | 2 | 1 | 0 |  | 67829268 |
| 19 | `0xf48490bf501a83abaf7406c6196a288f31c19a41` | 73.0 | 13 | 1 | 7 | 0 |  | 67829274 |
| 20 | `0x59a2f318f0ca90491ea966a24bcb2ee77ae12969` | 65.0 | 10 | 1 | 5 | 0 |  | 67829289 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
