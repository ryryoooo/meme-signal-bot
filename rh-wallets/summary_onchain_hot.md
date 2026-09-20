# RH on-chain hot active wallets

- Updated: **2026-09-20 21:07 JST**
- Window: blocks `67921883` … `67922682` (**800** scanned, tip=67922682)
- RPC calls: **1657** (429s=165)
- Elapsed: **310.1s**
- Total txs in window: **7795** · unique senders: **4290**
- Candidates (≥2 txs): **1395** · receipt-checked: **120** (receipts=856)
- Hot wallets: **135** (spam bots dropped: 0)
- Known-good CAs loaded: **1139** · watch overlap set: **2600**
- In-watch among hot: **10** · new discoveries: **125**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0xd91abf0e7a4fea07b1dde4eeb3fc3dc95ceafb56` | 132.0 | 27 | 4 | 8 | 0 |  | 67922649 |
| 2 | `0x18ea5238af7db263383c1076d3652b77e22c0347` | 125.0 | 26 | 4 | 5 | 0 |  | 67922374 |
| 3 | `0x0a0c68b4947c84582f293e52aff99f99de03a5be` | 78.0 | 8 | 4 | 4 | 0 |  | 67922599 |
| 4 | `0x338a3b6b0eabc0efdd1aea33e81e57c102158d8c` | 68.0 | 11 | 4 | 2 | 0 |  | 67922177 |
| 5 | `0xc617da044303fb14ac7f42e87fc1289020e82c9e` | 65.0 | 6 | 3 | 5 | 0 |  | 67922681 |
| 6 | `0x0b84c1743d4c5d5d1c9925dede1569c7bbbb331d` | 78.0 | 7 | 2 | 6 | 1 |  | 67922300 |
| 7 | `0x3286dd336a9fdf00b1b6f7d960c4440dc5a52009` | 77.0 | 11 | 2 | 7 | 0 |  | 67922578 |
| 8 | `0xaeddc7907127fb98b3d638c1f788935fd5c646e2` | 62.0 | 9 | 2 | 4 | 0 |  | 67922567 |
| 9 | `0x8b216cc896f32bcabca3649ca918d532f72010ef` | 46.0 | 5 | 2 | 2 | 0 |  | 67922589 |
| 10 | `0xe6012a285f3702aa6d7af7c3006b79fb2df10d08` | 46.0 | 9 | 2 | 2 | 0 |  | 67922178 |
| 11 | `0xd19750949328a577a0eb8157bf6defd82ee302b4` | 46.0 | 5 | 2 | 2 | 0 |  | 67921979 |
| 12 | `0x9f2f29f7a40a133c7abddc2a82715baa429546c6` | 46.0 | 5 | 2 | 2 | 0 |  | 67921975 |
| 13 | `0x416102d9a3eebd657222d1e4e33e93c6bf8508a8` | 44.0 | 6 | 2 | 2 | 0 |  | 67922520 |
| 14 | `0x3b1c68f26deb03aa58c0d344a861ed823b83dfc8` | 44.0 | 5 | 2 | 2 | 0 |  | 67922124 |
| 15 | `0x54387348eae930dc0eca622abcde1a62470fe94e` | 42.0 | 4 | 2 | 2 | 0 |  | 67922661 |
| 16 | `0x6b73ab1e4a4a7a7da757ac2ab89f0cf93fa291a5` | 42.0 | 4 | 2 | 2 | 0 |  | 67922532 |
| 17 | `0x511a69dab088f1885260882e12b0354fed3fac0c` | 42.0 | 4 | 2 | 2 | 0 |  | 67922449 |
| 18 | `0x047c81a5f1a06930142bf84a8b2eeaa2f30bda23` | 42.0 | 4 | 2 | 2 | 0 |  | 67922312 |
| 19 | `0xfd2a475d7ce02ec077867d72177063b5c2ab01b6` | 42.0 | 4 | 2 | 2 | 0 |  | 67922183 |
| 20 | `0x2081f3e6d328c4077d5cd2350f2c9e3916b27507` | 42.0 | 4 | 2 | 2 | 0 |  | 67921987 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
