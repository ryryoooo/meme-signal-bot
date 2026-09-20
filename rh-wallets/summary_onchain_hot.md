# RH on-chain hot active wallets

- Updated: **2026-09-20 16:31 JST**
- Window: blocks `67759504` … `67760303` (**800** scanned, tip=67760303)
- RPC calls: **1506** (429s=228)
- Elapsed: **221.6s**
- Total txs in window: **4086** · unique senders: **1847**
- Candidates (≥2 txs): **655** · receipt-checked: **120** (receipts=705)
- Hot wallets: **95** (spam bots dropped: 0)
- Known-good CAs loaded: **1139** · watch overlap set: **2802**
- In-watch among hot: **11** · new discoveries: **84**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0xba618977fe27a4d86663867d258491e953644da1` | 85.0 | 6 | 5 | 5 | 0 |  | 67760193 |
| 2 | `0xc216bfa5da000965e820845c32e6fd88db275743` | 72.0 | 4 | 5 | 4 | 0 |  | 67760232 |
| 3 | `0x062280e98e31b64c3ab06a1de88d1525a9420629` | 86.0 | 10 | 4 | 4 | 0 |  | 67760085 |
| 4 | `0xd1a8b5b354efa117b55b26e9682988009e948128` | 64.0 | 5 | 3 | 2 | 1 |  | 67760118 |
| 5 | `0x0963c944df0f2e99611068ce1156475866f1031c` | 60.0 | 5 | 3 | 4 | 0 |  | 67760086 |
| 6 | `0x6e2749b762cc048089dfdae19f7083e1f0e00fb7` | 61.0 | 6 | 3 | 3 | 0 |  | 67759670 |
| 7 | `0xa1ff26582fb245666b1af70ca73823feda64ad70` | 59.0 | 7 | 3 | 3 | 0 |  | 67760082 |
| 8 | `0xb24971b3b1760f4c03f6c05a8643cc081840fda0` | 59.0 | 7 | 3 | 3 | 0 |  | 67759796 |
| 9 | `0xd101922ff561f0940efbec3032210eb011daa091` | 48.0 | 5 | 3 | 2 | 0 |  | 67760284 |
| 10 | `0x3286dd336a9fdf00b1b6f7d960c4440dc5a52009` | 61.0 | 8 | 2 | 5 | 0 |  | 67760279 |
| 11 | `0x3b0ca12ed7ff5e8e2f67da045cf04a1731b63fcd` | 45.0 | 4 | 2 | 3 | 0 |  | 67760297 |
| 12 | `0x4123ffc81579e38dfff74b75d8bdf21c41e62237` | 52.0 | 3 | 2 | 2 | 0 | Y | 67759947 |
| 13 | `0x2b59f185e12fec98b8c17a932aee42a5611a06d9` | 50.0 | 7 | 2 | 2 | 0 |  | 67760070 |
| 14 | `0x62ef1b3c77dffbbbde9d3ed91ddb31a1b567d6de` | 46.0 | 6 | 2 | 2 | 0 |  | 67760278 |
| 15 | `0x88ca682c8dd3f6ee798d1dfac3ac90799a54381c` | 46.0 | 6 | 2 | 2 | 0 |  | 67760269 |
| 16 | `0xa8e878c77b4ddd628408fcc7e1d34a3c47a0d10e` | 39.0 | 5 | 2 | 1 | 0 |  | 67760125 |
| 17 | `0xa1b4fd1db06d8a2d091a3b91fcd0be9e0c220bc1` | 70.0 | 8 | 1 | 6 | 1 |  | 67759971 |
| 18 | `0x6abea4998f9ef34ee1cda5715ccca0e4d4efb730` | 53.0 | 5 | 1 | 1 | 1 | Y | 67759689 |
| 19 | `0x6f100b4b95163101740bab44e372457d1160d0e3` | 52.0 | 6 | 1 | 6 | 0 |  | 67760174 |
| 20 | `0x1bf3c4230b2feb7110328e0a1871b1194316ca20` | 37.0 | 5 | 1 | 3 | 0 |  | 67760147 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
