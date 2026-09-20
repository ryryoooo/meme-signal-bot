# RH on-chain hot active wallets

- Updated: **2026-09-20 18:45 JST**
- Window: blocks `67838050` … `67838849` (**800** scanned, tip=67838849)
- RPC calls: **1633** (429s=301)
- Elapsed: **294.4s**
- Total txs in window: **5784** · unique senders: **2703**
- Candidates (≥2 txs): **1033** · receipt-checked: **120** (receipts=832)
- Hot wallets: **103** (spam bots dropped: 1)
- Known-good CAs loaded: **1139** · watch overlap set: **2627**
- In-watch among hot: **5** · new discoveries: **98**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0x8b216cc896f32bcabca3649ca918d532f72010ef` | 63.0 | 6 | 3 | 3 | 0 |  | 67838655 |
| 2 | `0x0b9590f8bdf7fcd329e90d56f464eedc166311b1` | 63.0 | 7 | 3 | 3 | 0 |  | 67838620 |
| 3 | `0x21a41cc99204cb56a61066d2027dd2c0a0875802` | 57.0 | 5 | 3 | 3 | 0 |  | 67838720 |
| 4 | `0xbcfbb5ba743d8930b6333a04fe25bce2994e0c72` | 52.0 | 5 | 3 | 2 | 0 |  | 67838433 |
| 5 | `0x6a84577464a5f66a12c7d13170485dbead61c3ec` | 74.0 | 9 | 2 | 6 | 0 |  | 67838759 |
| 6 | `0x3286dd336a9fdf00b1b6f7d960c4440dc5a52009` | 59.0 | 7 | 2 | 5 | 0 |  | 67838829 |
| 7 | `0x141b4102b22457d397ebeb69fa6ea052f604c9b1` | 54.0 | 6 | 2 | 4 | 0 |  | 67838824 |
| 8 | `0xe05932091f1c2a4853159828b94722948c45513e` | 56.0 | 4 | 2 | 2 | 0 | Y | 67838650 |
| 9 | `0x2a4d34cd09a36f59ae3bedc0880cd5da929321d7` | 56.0 | 4 | 2 | 2 | 0 | Y | 67838251 |
| 10 | `0x7ff6a60102c1a36e260c382f9d1c15f1a5814e9c` | 50.0 | 7 | 2 | 2 | 0 |  | 67838384 |
| 11 | `0x9bf265c742d96aacf1083ef24c9110240af0bea9` | 42.0 | 4 | 2 | 2 | 0 |  | 67838794 |
| 12 | `0x511a69dab088f1885260882e12b0354fed3fac0c` | 42.0 | 4 | 2 | 2 | 0 |  | 67838632 |
| 13 | `0x3bc344b39459521932d3583f1131d224769d496e` | 42.0 | 4 | 2 | 2 | 0 |  | 67838458 |
| 14 | `0x6f34a18d370aa8235e8f8449de82e0f129b61515` | 42.0 | 5 | 2 | 2 | 0 |  | 67838448 |
| 15 | `0x53e68553ca08f512423628d64c01b0a14dfcda99` | 42.0 | 4 | 2 | 2 | 0 |  | 67838233 |
| 16 | `0x7c6864a4da97542b6c52ab52feb67082b7f23a40` | 40.0 | 4 | 2 | 2 | 0 |  | 67838804 |
| 17 | `0x76b724c2721d50164f0f0959da5f8be283553bf6` | 40.0 | 4 | 2 | 2 | 0 |  | 67838653 |
| 18 | `0xc341d2427dff37305cd2d970e68f3c52c30766f2` | 38.0 | 3 | 2 | 2 | 0 |  | 67838452 |
| 19 | `0x8ab3eb59c3bf3bdd98b379fa74c41111f070f955` | 65.0 | 6 | 1 | 3 | 1 | Y | 67838699 |
| 20 | `0x59a2f318f0ca90491ea966a24bcb2ee77ae12969` | 76.0 | 12 | 1 | 6 | 0 |  | 67838841 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
