# RH on-chain hot active wallets

- Updated: **2026-09-20 16:47 JST**
- Window: blocks `67768663` … `67769462` (**800** scanned, tip=67769462)
- RPC calls: **1472** (429s=162)
- Elapsed: **198.4s**
- Total txs in window: **3905** · unique senders: **1687**
- Candidates (≥2 txs): **588** · receipt-checked: **120** (receipts=671)
- Hot wallets: **91** (spam bots dropped: 1)
- Known-good CAs loaded: **1139** · watch overlap set: **2812**
- In-watch among hot: **6** · new discoveries: **85**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0xba618977fe27a4d86663867d258491e953644da1` | 135.0 | 12 | 7 | 9 | 0 |  | 67769063 |
| 2 | `0xfd2a475d7ce02ec077867d72177063b5c2ab01b6` | 138.0 | 13 | 4 | 6 | 2 | Y | 67769440 |
| 3 | `0x55454b47051b2b418600dd76f81d97d475d8b988` | 102.0 | 12 | 4 | 6 | 0 |  | 67769076 |
| 4 | `0x062280e98e31b64c3ab06a1de88d1525a9420629` | 86.0 | 10 | 4 | 4 | 0 |  | 67768894 |
| 5 | `0x0963c944df0f2e99611068ce1156475866f1031c` | 64.0 | 6 | 3 | 4 | 0 |  | 67769237 |
| 6 | `0x0a0c68b4947c84582f293e52aff99f99de03a5be` | 57.0 | 5 | 3 | 3 | 0 |  | 67769453 |
| 7 | `0xaa0463123d19b3316cf4c512db31909e3df31e37` | 55.0 | 4 | 3 | 3 | 0 |  | 67769206 |
| 8 | `0xdb01c8093c19a3bb98fa3d01995953ea09840de3` | 51.0 | 3 | 3 | 3 | 0 |  | 67769316 |
| 9 | `0xcc2e68e7bcfd29a894ada6d3016559081fac27ac` | 52.0 | 5 | 3 | 2 | 0 |  | 67769248 |
| 10 | `0x4b2048442793474a180eaceeda85afd277112e54` | 52.0 | 4 | 2 | 2 | 1 |  | 67769230 |
| 11 | `0xc3c2043e9d3b2e549b1c2fa0dd594852d233820f` | 91.0 | 18 | 2 | 7 | 0 |  | 67769184 |
| 12 | `0x3286dd336a9fdf00b1b6f7d960c4440dc5a52009` | 68.0 | 9 | 2 | 6 | 0 |  | 67769210 |
| 13 | `0xedb2348640113524a92cf1553e8d02f333806bdd` | 58.0 | 8 | 2 | 4 | 0 |  | 67769175 |
| 14 | `0x4304b5c80eab9abeb75246591c78daa2af242dbf` | 88.0 | 22 | 2 | 2 | 0 |  | 67769400 |
| 15 | `0x9f2f29f7a40a133c7abddc2a82715baa429546c6` | 46.0 | 5 | 2 | 2 | 0 |  | 67769415 |
| 16 | `0x976a2afdff03785306f5357803ab1a2b76d3f6a3` | 44.0 | 5 | 2 | 2 | 0 |  | 67768925 |
| 17 | `0x8300f444462e55d11dba5823a1584badfc293fd9` | 40.0 | 4 | 2 | 2 | 0 |  | 67769445 |
| 18 | `0x9909d019032fbaa169ecad03b38c17d8a2a9d1f8` | 38.0 | 4 | 2 | 2 | 0 |  | 67769410 |
| 19 | `0xcb62ed0bdd1465e6f6026a2cad900406838c7913` | 38.0 | 3 | 2 | 2 | 0 |  | 67769042 |
| 20 | `0xde88b15c2939b58990a7784d9810ef8229b5c060` | 38.0 | 3 | 2 | 2 | 0 |  | 67768754 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
