# RH on-chain hot active wallets

- Updated: **2026-09-20 17:38 JST**
- Window: blocks `67798732` … `67799531` (**800** scanned, tip=67799531)
- RPC calls: **1639** (429s=281)
- Elapsed: **274.2s**
- Total txs in window: **5065** · unique senders: **2026**
- Candidates (≥2 txs): **791** · receipt-checked: **120** (receipts=838)
- Hot wallets: **115** (spam bots dropped: 1)
- Known-good CAs loaded: **1139** · watch overlap set: **2621**
- In-watch among hot: **3** · new discoveries: **112**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0xb27c000af781f4d4a6daf099dfd558694da2aeb1` | 152.0 | 10 | 10 | 10 | 0 |  | 67799516 |
| 2 | `0x046c58b10eef3b19154da71955815f05c0010a8a` | 97.0 | 14 | 5 | 5 | 0 |  | 67799274 |
| 3 | `0xba618977fe27a4d86663867d258491e953644da1` | 83.0 | 6 | 5 | 5 | 0 |  | 67799472 |
| 4 | `0x18dff957ab3fb5f5742dd67ee298b101464e7bf9` | 65.0 | 6 | 3 | 5 | 0 |  | 67799381 |
| 5 | `0x1132a53eeed9e9bcaef8e7556ece2ca2267f58ac` | 61.0 | 6 | 3 | 3 | 0 |  | 67799268 |
| 6 | `0x8b216cc896f32bcabca3649ca918d532f72010ef` | 59.0 | 6 | 3 | 3 | 0 |  | 67799502 |
| 7 | `0x04b189d4b4b106c0aa43a4b2007f715232e57b00` | 57.0 | 5 | 3 | 3 | 0 |  | 67799172 |
| 8 | `0x7af2fda13652abb00e02d1bc655fb3884498ac30` | 80.0 | 8 | 2 | 6 | 1 |  | 67798990 |
| 9 | `0x3286dd336a9fdf00b1b6f7d960c4440dc5a52009` | 79.0 | 12 | 2 | 7 | 0 |  | 67799509 |
| 10 | `0xdf81c79f05a96af3d19f2f22d7679d7846f262f8` | 68.0 | 9 | 2 | 4 | 0 |  | 67799240 |
| 11 | `0x338a3b6b0eabc0efdd1aea33e81e57c102158d8c` | 119.0 | 68 | 2 | 2 | 0 |  | 67799526 |
| 12 | `0x4ede1e93e24aa6466a376bdfd538c3a7f902f211` | 44.0 | 5 | 2 | 2 | 0 |  | 67799504 |
| 13 | `0xa2e5f895953592895665ceb468d65d260d74eebf` | 40.0 | 4 | 2 | 2 | 0 |  | 67799338 |
| 14 | `0x629b9796c288e6fec8de8239e13cc9a5f51f03f6` | 40.0 | 4 | 2 | 2 | 0 |  | 67799286 |
| 15 | `0x2a5a8f4243f669c1c31df524adbf7f72b4d231ad` | 38.0 | 3 | 2 | 2 | 0 |  | 67799463 |
| 16 | `0x66705faaa2067e7e8ab46db39de9c67e8a4b6857` | 38.0 | 3 | 2 | 2 | 0 |  | 67799396 |
| 17 | `0xfdd4c876dc8dff7fbf305aa5933617f0fddb5049` | 38.0 | 3 | 2 | 2 | 0 |  | 67799348 |
| 18 | `0x409a9cf1f7d7c0167653efd760c6c36ac078fdc2` | 38.0 | 3 | 2 | 2 | 0 |  | 67799344 |
| 19 | `0x141b4102b22457d397ebeb69fa6ea052f604c9b1` | 38.0 | 3 | 2 | 2 | 0 |  | 67799313 |
| 20 | `0xd4e3f654c2a4b4bb778e712feb214e87d35b0f2d` | 39.0 | 6 | 2 | 1 | 0 |  | 67798926 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
