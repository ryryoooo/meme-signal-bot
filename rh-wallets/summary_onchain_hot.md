# RH on-chain hot active wallets

- Updated: **2026-09-20 16:16 JST**
- Window: blocks `67750816` … `67751615` (**800** scanned, tip=67751615)
- RPC calls: **1428** (429s=234)
- Elapsed: **195.4s**
- Total txs in window: **3925** · unique senders: **1873**
- Candidates (≥2 txs): **592** · receipt-checked: **120** (receipts=627)
- Hot wallets: **90** (spam bots dropped: 0)
- Known-good CAs loaded: **1139** · watch overlap set: **2785**
- In-watch among hot: **4** · new discoveries: **86**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0x1d5131e79e9d2cc9d4b03e63eb473d40eeafa198` | 148.0 | 19 | 5 | 12 | 0 |  | 67751613 |
| 2 | `0x046c58b10eef3b19154da71955815f05c0010a8a` | 112.0 | 20 | 5 | 4 | 0 |  | 67750990 |
| 3 | `0xfd2a475d7ce02ec077867d72177063b5c2ab01b6` | 128.0 | 14 | 4 | 6 | 2 |  | 67751597 |
| 4 | `0xd7a6c07bdf014f5850e30f36be972b7dc83b7142` | 48.0 | 4 | 3 | 2 | 0 |  | 67751576 |
| 5 | `0x3286dd336a9fdf00b1b6f7d960c4440dc5a52009` | 61.0 | 8 | 2 | 5 | 0 |  | 67751605 |
| 6 | `0x4501120e233a7c639d5052c80f2da950b3cdb526` | 55.0 | 5 | 2 | 5 | 0 |  | 67751015 |
| 7 | `0xba1c8c5889b9c225de827386620694f3c2a0b444` | 41.0 | 3 | 2 | 3 | 0 |  | 67751587 |
| 8 | `0xb303fae020bf39fa1581f43ff6080dbcf3a0d9c0` | 46.0 | 5 | 2 | 2 | 0 |  | 67751567 |
| 9 | `0xbe00750dca114c9006d36b8b8b68de2802474898` | 46.0 | 6 | 2 | 2 | 0 |  | 67751480 |
| 10 | `0x6b73ab1e4a4a7a7da757ac2ab89f0cf93fa291a5` | 46.0 | 5 | 2 | 2 | 0 |  | 67751313 |
| 11 | `0x0fb04715ca664193293bf09b901676bd0a8b8888` | 42.0 | 4 | 2 | 2 | 0 |  | 67751280 |
| 12 | `0x9c44dc22462e1a3116b21df86395e70f5e194e59` | 42.0 | 4 | 2 | 2 | 0 |  | 67751171 |
| 13 | `0xf726427085f6a5dc85ba8e835eb9d8d3c027c7f8` | 40.0 | 4 | 2 | 2 | 0 |  | 67751545 |
| 14 | `0x1561505700bf373662a9d9df04a604dc617027d9` | 40.0 | 4 | 2 | 2 | 0 |  | 67751490 |
| 15 | `0x37062e6ba9f1c6efdd575dba74f8d904161c5e68` | 37.0 | 4 | 2 | 1 | 0 |  | 67751159 |
| 16 | `0x6d0ecf8770d2de3cc5412dad5ea1dda890d109f0` | 39.0 | 2 | 1 | 1 | 1 | Y | 67751392 |
| 17 | `0x51a0ff21294bd789134a279091fad9a7d4c5bc65` | 37.0 | 3 | 1 | 1 | 1 |  | 67751590 |
| 18 | `0x08d65ec566a94fa7913266963dc455892b9f1449` | 56.0 | 8 | 1 | 4 | 0 |  | 67751213 |
| 19 | `0x3d9dd11aab08d23e3d51d8c9f8aa81b7744fe7e1` | 41.0 | 6 | 1 | 3 | 0 |  | 67751577 |
| 20 | `0xf70da97812cb96acdf810712aa562db8dfa3dbef` | 40.0 | 7 | 1 | 2 | 0 |  | 67751438 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
