# RH on-chain hot active wallets

- Updated: **2026-09-20 17:07 JST**
- Window: blocks `67779962` … `67780761` (**800** scanned, tip=67780761)
- RPC calls: **1701** (429s=289)
- Elapsed: **299.3s**
- Total txs in window: **10974** · unique senders: **2795**
- Candidates (≥2 txs): **1178** · receipt-checked: **120** (receipts=900)
- Hot wallets: **105** (spam bots dropped: 35)
- Known-good CAs loaded: **1139** · watch overlap set: **2827**
- In-watch among hot: **5** · new discoveries: **100**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0xaa4dfdb453d8fe3a83c9f9ead0711222bcd638dd` | 83.0 | 13 | 3 | 5 | 0 |  | 67780700 |
| 2 | `0xdcb9ca9f8c9120c4251549a15407ad17bf1ff083` | 85.0 | 29 | 1 | 3 | 0 |  | 67780744 |
| 3 | `0x6abea4998f9ef34ee1cda5715ccca0e4d4efb730` | 55.0 | 12 | 1 | 1 | 0 | Y | 67780658 |
| 4 | `0x49bbf2b70955fb3a106e084d4bfda92d334573d2` | 115.0 | 108 | 0 | 0 | 0 |  | 67780717 |
| 5 | `0x0df6c565a49f8fcc02251836236f980d304a3930` | 74.0 | 28 | 0 | 0 | 0 | Y | 67780728 |
| 6 | `0x0eae43fa3ea25c7443664e6a930be711383d98ad` | 54.0 | 18 | 0 | 0 | 0 | Y | 67780729 |
| 7 | `0x2ab6bbae2cc25355a11bf4ccf956f5b94ba65317` | 52.0 | 23 | 0 | 0 | 0 |  | 67780726 |
| 8 | `0x7b2daf7f696bb844c7786693062d6619d1858cc9` | 50.0 | 17 | 0 | 0 | 0 |  | 67780678 |
| 9 | `0xcf392f62151fe078a5c26c6fd7e6bc5f15d9735a` | 34.0 | 6 | 0 | 0 | 0 | Y | 67780690 |
| 10 | `0x2a4d34cd09a36f59ae3bedc0880cd5da929321d7` | 28.0 | 4 | 0 | 0 | 0 | Y | 67780204 |
| 11 | `0x461b9ef35a73f498b4f05c0dc8e0c2988d6d24ce` | 28.0 | 9 | 0 | 0 | 0 |  | 67780160 |
| 12 | `0xbc7af78d5865ecd1c4abc17cb2d28cc59b470130` | 26.0 | 11 | 0 | 0 | 0 |  | 67780746 |
| 13 | `0xb8ff877ed78ba520ece21b1de7843a8a57ca47cb` | 24.0 | 8 | 0 | 0 | 0 |  | 67780761 |
| 14 | `0x1c20a623e9b37d17397eea513aa1afe2fd26c775` | 24.0 | 6 | 0 | 0 | 0 |  | 67780760 |
| 15 | `0x4381045063b19a615a272733fa6b5f169b622646` | 24.0 | 10 | 0 | 0 | 0 |  | 67780750 |
| 16 | `0xada5bb90d0de0bd1b6f3938708f49295a8d1f7cb` | 24.0 | 9 | 0 | 0 | 0 |  | 67780690 |
| 17 | `0xd9497b53abcd6972b6370cb195b6bfd6b280e607` | 24.0 | 10 | 0 | 0 | 0 |  | 67780641 |
| 18 | `0xf9d33da08b05e3a78b878ebbedc50485d6e49374` | 24.0 | 7 | 0 | 0 | 0 |  | 67780640 |
| 19 | `0x9b51bc6ae99d8e803f1f1c38f14cff60f2e0e7fc` | 24.0 | 6 | 0 | 0 | 0 |  | 67780621 |
| 20 | `0x9f1d64708ceca3598e10d06d95db7d9cd74ad787` | 24.0 | 7 | 0 | 0 | 0 |  | 67780395 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
