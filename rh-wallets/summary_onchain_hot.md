# RH on-chain hot active wallets

- Updated: **2026-09-20 19:33 JST**
- Window: blocks `67865999` … `67866798` (**800** scanned, tip=67866798)
- RPC calls: **1487** (429s=286)
- Elapsed: **342.8s**
- Total txs in window: **4828** · unique senders: **2085**
- Candidates (≥2 txs): **967** · receipt-checked: **120** (receipts=686)
- Hot wallets: **118** (spam bots dropped: 1)
- Known-good CAs loaded: **1139** · watch overlap set: **2589**
- In-watch among hot: **2** · new discoveries: **116**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0xba618977fe27a4d86663867d258491e953644da1` | 133.0 | 13 | 5 | 11 | 0 |  | 67866366 |
| 2 | `0x8b216cc896f32bcabca3649ca918d532f72010ef` | 61.0 | 6 | 3 | 3 | 0 |  | 67866789 |
| 3 | `0x8f08ae9cc7025343bf4e2dde620ddc56a2f504a8` | 86.0 | 8 | 2 | 8 | 1 |  | 67866790 |
| 4 | `0x468f854bc1ab40f83576efdad4f44e879ad57c64` | 64.0 | 5 | 2 | 4 | 1 |  | 67866769 |
| 5 | `0x3286dd336a9fdf00b1b6f7d960c4440dc5a52009` | 68.0 | 9 | 2 | 6 | 0 |  | 67866666 |
| 6 | `0x9f2f29f7a40a133c7abddc2a82715baa429546c6` | 84.0 | 16 | 2 | 4 | 0 |  | 67866601 |
| 7 | `0xfff61eb7dbad7271d3f3f15f3d71564278e58e13` | 44.0 | 6 | 2 | 2 | 0 |  | 67866726 |
| 8 | `0x76f0781b4142412d46b8d2b44df7f52a8c715b25` | 44.0 | 5 | 2 | 2 | 0 |  | 67866319 |
| 9 | `0x35ad2b9e33ba4d448c2d2387135a164dd86c01ab` | 44.0 | 5 | 2 | 2 | 0 |  | 67866153 |
| 10 | `0xd1df06767842f9222746facaa191446c9f473cc9` | 42.0 | 4 | 2 | 2 | 0 |  | 67866759 |
| 11 | `0x0ef9f45c35f09452b4fb6ab73629922cca5c42bf` | 37.0 | 3 | 1 | 1 | 1 |  | 67866503 |
| 12 | `0x2e53357d75a83da7f49b369dd10482152bee3c57` | 79.0 | 11 | 1 | 9 | 0 |  | 67866609 |
| 13 | `0x3ddb3571e81e123dbc74c1f790c33576cd66403d` | 56.0 | 7 | 1 | 6 | 0 |  | 67866673 |
| 14 | `0xa9d85386050ef91d65da21b6d0362f757c6b4ef2` | 48.0 | 9 | 1 | 4 | 0 |  | 67866795 |
| 15 | `0x78e930671cc95154cc7b8b72b93127bb4a0e2031` | 41.0 | 7 | 1 | 3 | 0 |  | 67866647 |
| 16 | `0xe2c0d4d1af6f893d4655a88d229cfe6f9a6e9519` | 41.0 | 7 | 1 | 3 | 0 |  | 67866646 |
| 17 | `0xe584e36e20f60b92a9a77310745cf0a54b6ba9e1` | 39.0 | 6 | 1 | 3 | 0 |  | 67866646 |
| 18 | `0x1340dcd1e1a24b8a6cdb3193da3fb13f6a8ca2a4` | 39.0 | 6 | 1 | 3 | 0 |  | 67866617 |
| 19 | `0x4baf6e6ccd15b5c218deb5f08d513084a7d7f6a4` | 39.0 | 6 | 1 | 3 | 0 |  | 67866567 |
| 20 | `0xa458f074b691db9e02a826ebb5aa890c8815a1d7` | 37.0 | 5 | 1 | 3 | 0 |  | 67866638 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
