# RH on-chain hot active wallets

- Updated: **2026-09-20 05:24 JST**
- Window: blocks `67362857` … `67363656` (**800** scanned, tip=67363656)
- RPC calls: **1301** (429s=212)
- Elapsed: **284.8s**
- Total txs in window: **11518** · unique senders: **5381**
- Candidates (≥2 txs): **1800** · receipt-checked: **80** (receipts=500)
- Hot wallets: **176** (spam bots dropped: 3)
- Known-good CAs loaded: **1134** · watch overlap set: **2567**
- In-watch among hot: **9** · new discoveries: **167**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0xd657bf12b59dfde3573c521abc3b111b62dfe3c5` | 92.0 | 7 | 4 | 4 | 0 | Y | 67363526 |
| 2 | `0x9976b6d898bcbf147a00179de51d8e77fccfd8cc` | 82.0 | 9 | 4 | 4 | 0 |  | 67363446 |
| 3 | `0x5136067ccc0065c20ac16cd7191d08564e19d71e` | 73.0 | 11 | 3 | 3 | 0 |  | 67363629 |
| 4 | `0x2abdd72b5bb40ba0df894d33d40aee918da551ae` | 73.0 | 11 | 3 | 3 | 0 |  | 67363629 |
| 5 | `0x82c8c413f7da04726c2c6a9bc044775134da6e4a` | 74.0 | 14 | 3 | 2 | 0 |  | 67363643 |
| 6 | `0xbe9c797a2a7e5b1bf27ae2b0539bf6365665d9f6` | 82.0 | 18 | 2 | 4 | 0 |  | 67363337 |
| 7 | `0x1eb26134126048244cac12c3a997d9952e4a9c77` | 47.0 | 8 | 2 | 1 | 0 |  | 67363068 |
| 8 | `0x9a148a23ff4cb17fe44248f8754bf063fcbb4dc0` | 76.0 | 16 | 1 | 4 | 1 |  | 67363141 |
| 9 | `0x6f950ecd14f38f3abc33f94321c0f4694488c72c` | 98.0 | 21 | 1 | 8 | 0 |  | 67363581 |
| 10 | `0x623265cb986174e0c90d54c0b93153651bf9c466` | 81.0 | 18 | 1 | 7 | 0 |  | 67363656 |
| 11 | `0x7874cf5bb7cf3bff1a66c22abd5a766649439be7` | 63.0 | 12 | 1 | 5 | 0 |  | 67363405 |
| 12 | `0x387f4ef175790d917204e0af93e84f666eb4bd02` | 62.0 | 14 | 1 | 4 | 0 |  | 67363422 |
| 13 | `0x7498d4001d4cd1059170c259b8561a70b643617e` | 60.0 | 13 | 1 | 4 | 0 |  | 67363401 |
| 14 | `0xd983b79aefce50d6a8f3077a84912cebc83f2843` | 53.0 | 12 | 1 | 3 | 0 |  | 67363353 |
| 15 | `0x4015b9c664ca83f7d26ae94560576b385e26771c` | 51.0 | 11 | 1 | 3 | 0 |  | 67363439 |
| 16 | `0xf70da97812cb96acdf810712aa562db8dfa3dbef` | 95.0 | 31 | 1 | 1 | 0 |  | 67363497 |
| 17 | `0x8bc8aecab84e3a5d64215b9a777d9f2b31063fc8` | 63.0 | 18 | 1 | 1 | 0 |  | 67363438 |
| 18 | `0x26558f89f952e9f557d91be898e7adcdcfb8ade6` | 61.0 | 22 | 1 | 1 | 0 |  | 67363495 |
| 19 | `0x43f92f3124ffe1f8c6c325351e5d546b26e2bb86` | 51.0 | 15 | 1 | 1 | 0 |  | 67363544 |
| 20 | `0x49bbf2b70955fb3a106e084d4bfda92d334573d2` | 115.0 | 140 | 0 | 0 | 0 |  | 67363654 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
