# RH on-chain hot active wallets

- Updated: **2026-09-20 17:23 JST**
- Window: blocks `67789708` … `67790507` (**800** scanned, tip=67790507)
- RPC calls: **1701** (429s=232)
- Elapsed: **275.0s**
- Total txs in window: **7152** · unique senders: **2966**
- Candidates (≥2 txs): **1178** · receipt-checked: **120** (receipts=900)
- Hot wallets: **128** (spam bots dropped: 0)
- Known-good CAs loaded: **1139** · watch overlap set: **2857**
- In-watch among hot: **3** · new discoveries: **125**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0x097ba31b7acffd75b909fc7bef2e55424d2dacdc` | 79.0 | 8 | 4 | 5 | 0 |  | 67790331 |
| 2 | `0xf407871e7dc2e7ba2edc8d2d69218e4ea13f26ec` | 72.0 | 6 | 4 | 4 | 0 |  | 67790235 |
| 3 | `0xd19750949328a577a0eb8157bf6defd82ee302b4` | 52.0 | 5 | 3 | 2 | 0 |  | 67790285 |
| 4 | `0x9f2f29f7a40a133c7abddc2a82715baa429546c6` | 52.0 | 5 | 3 | 2 | 0 |  | 67790210 |
| 5 | `0x338a3b6b0eabc0efdd1aea33e81e57c102158d8c` | 116.5 | 66 | 2 | 2 | 0 |  | 67790498 |
| 6 | `0x10d25727a579e16eb11a73265506e88eb011784b` | 48.0 | 6 | 2 | 2 | 0 |  | 67790268 |
| 7 | `0xfd93dba5a43f0f276657fe0371b9e8031677cdb1` | 48.0 | 6 | 2 | 2 | 0 |  | 67789868 |
| 8 | `0xdd17e9208f1172b360e8e677843811fdebf60671` | 44.0 | 5 | 2 | 2 | 0 |  | 67789971 |
| 9 | `0xb648b79acbe74828926fdd015594b65944b257bd` | 42.0 | 4 | 2 | 2 | 0 |  | 67790145 |
| 10 | `0xb42522dc545a7b8106f34ed400618eefc051b89c` | 94.0 | 12 | 1 | 12 | 0 |  | 67790427 |
| 11 | `0x15a006e8f7f02a730c81bf0ba88f25e21b2f297b` | 58.0 | 13 | 1 | 4 | 0 |  | 67790482 |
| 12 | `0x0fbd8620d29012a60689ccabda7d269d492db4e7` | 56.0 | 12 | 1 | 4 | 0 |  | 67790391 |
| 13 | `0xf70da97812cb96acdf810712aa562db8dfa3dbef` | 83.0 | 25 | 1 | 1 | 0 |  | 67790446 |
| 14 | `0x88b2139cb2557da2b60096b24628cc0f6ed669eb` | 35.0 | 7 | 1 | 1 | 0 |  | 67790488 |
| 15 | `0x2b38ecb45370d73aa816426d9eb2de0dc0650dd5` | 35.0 | 7 | 1 | 1 | 0 |  | 67790486 |
| 16 | `0x4bc1f4b5d6d8059b3aa2b8f318d1a53430d4c2a5` | 33.0 | 6 | 1 | 1 | 0 |  | 67790284 |
| 17 | `0x1ed497c019385347ed2a4e1e253c983f76c2102b` | 33.0 | 6 | 1 | 1 | 0 |  | 67790234 |
| 18 | `0x58b423e28c07d2c7af97c35017b343ac227cf01a` | 33.0 | 6 | 1 | 1 | 0 |  | 67790133 |
| 19 | `0xeb0fb059c41fe45ef8019046eef59c7aa4128781` | 33.0 | 6 | 1 | 1 | 0 |  | 67789979 |
| 20 | `0x02ac7f439680c865d45a1339fce4564e75aa74da` | 31.0 | 5 | 1 | 1 | 0 |  | 67790389 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
