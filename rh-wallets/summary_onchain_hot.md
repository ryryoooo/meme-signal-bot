# RH on-chain hot active wallets

- Updated: **2026-09-20 14:48 JST**
- Window: blocks `67698525` … `67699324` (**800** scanned, tip=67699324)
- RPC calls: **1477** (429s=240)
- Elapsed: **221.5s**
- Total txs in window: **3490** · unique senders: **1341**
- Candidates (≥2 txs): **499** · receipt-checked: **120** (receipts=676)
- Hot wallets: **91** (spam bots dropped: 0)
- Known-good CAs loaded: **1139** · watch overlap set: **2577**
- In-watch among hot: **4** · new discoveries: **87**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0xb9799e89593b3dafb625c74c3fd0a5fba6b87c98` | 144.0 | 13 | 6 | 10 | 0 |  | 67699178 |
| 2 | `0xc207df8dc9ee5113c6374c70c2a29bb87e94cfdf` | 122.0 | 9 | 4 | 8 | 2 |  | 67699312 |
| 3 | `0x03aebf90342f923c9438f9cb55ecf8a4dd8719b5` | 53.0 | 4 | 3 | 3 | 0 |  | 67699028 |
| 4 | `0xaf93f17687721989af9836b239dca82b097f7f94` | 50.0 | 4 | 3 | 2 | 0 |  | 67698630 |
| 5 | `0x16da093ef481eef6ad36ee65970f489f4e206d59` | 64.0 | 8 | 2 | 2 | 1 |  | 67698733 |
| 6 | `0x3286dd336a9fdf00b1b6f7d960c4440dc5a52009` | 61.0 | 8 | 2 | 5 | 0 |  | 67699123 |
| 7 | `0x142a12a553b590e76d47fceb9e671ce7b1d20c20` | 57.0 | 9 | 2 | 3 | 0 |  | 67698618 |
| 8 | `0x511abacd5ea5ec09274f1026261940bc759a508b` | 49.0 | 5 | 2 | 3 | 0 |  | 67699129 |
| 9 | `0xd1df06767842f9222746facaa191446c9f473cc9` | 42.0 | 4 | 2 | 2 | 0 |  | 67699282 |
| 10 | `0x7f0f9250f15e6102b6b49e39916422c505af1301` | 38.0 | 4 | 2 | 2 | 0 |  | 67699306 |
| 11 | `0xe00a78e4d4f8e1a2d666b10f0a4846096488a6a0` | 38.0 | 3 | 2 | 2 | 0 |  | 67699172 |
| 12 | `0x8b216cc896f32bcabca3649ca918d532f72010ef` | 38.0 | 3 | 2 | 2 | 0 |  | 67698909 |
| 13 | `0x13dd80ea5d02cee7b8d10ae90f877adbbd07107a` | 38.0 | 4 | 2 | 2 | 0 |  | 67698908 |
| 14 | `0x1bf3c4230b2feb7110328e0a1871b1194316ca20` | 44.0 | 4 | 1 | 2 | 1 |  | 67699025 |
| 15 | `0x306d4de91283c22e6cc73774080c5e33272b30ae` | 80.0 | 12 | 1 | 6 | 0 | Y | 67698982 |
| 16 | `0xdde926b5e591bf906ff352536c2eec27d929cd50` | 62.0 | 10 | 1 | 6 | 0 |  | 67699124 |
| 17 | `0x2e6c7e45d928cbe3c92334541f28ad5590f115bb` | 53.0 | 7 | 1 | 5 | 0 |  | 67699267 |
| 18 | `0xf63e63a80a25611154c5d1c06e55fd763e0cfc19` | 90.0 | 22 | 1 | 4 | 0 |  | 67699324 |
| 19 | `0xba618977fe27a4d86663867d258491e953644da1` | 43.0 | 6 | 1 | 3 | 0 |  | 67698901 |
| 20 | `0xb86e63cd3c2821ad1e34721e1c8e4a2195544577` | 42.0 | 3 | 1 | 2 | 0 | Y | 67699087 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
