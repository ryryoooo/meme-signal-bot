# RH on-chain hot active wallets

- Updated: **2026-09-20 14:32 JST**
- Window: blocks `67689487` … `67690286` (**800** scanned, tip=67690286)
- RPC calls: **1466** (429s=190)
- Elapsed: **182.0s**
- Total txs in window: **3900** · unique senders: **1799**
- Candidates (≥2 txs): **543** · receipt-checked: **120** (receipts=665)
- Hot wallets: **76** (spam bots dropped: 0)
- Known-good CAs loaded: **1134** · watch overlap set: **2629**
- In-watch among hot: **3** · new discoveries: **73**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0xb9799e89593b3dafb625c74c3fd0a5fba6b87c98` | 152.0 | 13 | 7 | 10 | 0 |  | 67690257 |
| 2 | `0xbe19ca0a7d41f3ea6a007d80b4f1c9ef725cd6c8` | 109.0 | 14 | 3 | 7 | 1 |  | 67690246 |
| 3 | `0x8b216cc896f32bcabca3649ca918d532f72010ef` | 75.0 | 8 | 3 | 3 | 1 |  | 67690014 |
| 4 | `0x8f612edf9bb0e3ca8719341ed7c896fc46e54b66` | 49.0 | 3 | 3 | 3 | 0 |  | 67689635 |
| 5 | `0x3286dd336a9fdf00b1b6f7d960c4440dc5a52009` | 66.0 | 8 | 2 | 6 | 0 |  | 67690279 |
| 6 | `0x18c7fdc3f6ccb04624a9133854839cf2370a3352` | 57.0 | 8 | 2 | 3 | 0 |  | 67690241 |
| 7 | `0xdb62fe4015a01e8d646390db41de11b956983ea3` | 42.0 | 4 | 2 | 2 | 0 |  | 67690031 |
| 8 | `0x851f7af1e585c3fe00156d622d0235f639f53339` | 42.0 | 4 | 2 | 2 | 0 |  | 67689784 |
| 9 | `0xd1df06767842f9222746facaa191446c9f473cc9` | 42.0 | 4 | 2 | 2 | 0 |  | 67689744 |
| 10 | `0x82f7ca98ac11cd94690b070cda91e6d4c303de18` | 42.0 | 4 | 2 | 2 | 0 |  | 67689698 |
| 11 | `0x9de4e64d173b040aef4b226ccdcc6004318ba48f` | 40.0 | 4 | 2 | 2 | 0 |  | 67690174 |
| 12 | `0x1579eb1c597f594b4d44b5e34368ebba4e3a207a` | 38.0 | 3 | 2 | 2 | 0 |  | 67690276 |
| 13 | `0xe7e7fd26cb859de6d1411706ddfce38d563ccf83` | 38.0 | 3 | 2 | 2 | 0 |  | 67689558 |
| 14 | `0x2398e6edb795ea753e487930202d9fcd82111614` | 93.0 | 13 | 1 | 11 | 0 |  | 67690179 |
| 15 | `0x20aa382c7570e4331bff70844df860b87f23e845` | 49.0 | 6 | 1 | 5 | 0 |  | 67690162 |
| 16 | `0x5a03cde73a4b3296908778b2ec6efd506b2694d3` | 50.0 | 7 | 1 | 4 | 0 |  | 67690228 |
| 17 | `0x11564fd96df2bdcd11326e070ea6bb54d9951fc8` | 34.0 | 5 | 1 | 2 | 0 |  | 67690228 |
| 18 | `0xb6f8b683dd999593964e073c11788e49a56c43de` | 57.0 | 20 | 1 | 1 | 0 |  | 67689646 |
| 19 | `0xd7c7f74d13fa65a090da54f1aede861ded73640e` | 37.0 | 3 | 1 | 1 | 0 | Y | 67690044 |
| 20 | `0x8ad557e25766c99e28e6fe8e3e0fbf87e452b23d` | 33.0 | 5 | 1 | 1 | 0 |  | 67689853 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
