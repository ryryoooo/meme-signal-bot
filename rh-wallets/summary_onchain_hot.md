# RH on-chain hot active wallets

- Updated: **2026-09-20 06:18 JST**
- Window: blocks `67394410` … `67395209` (**800** scanned, tip=67395209)
- RPC calls: **1701** (429s=303)
- Elapsed: **350.2s**
- Total txs in window: **10961** · unique senders: **3289**
- Candidates (≥2 txs): **1374** · receipt-checked: **120** (receipts=900)
- Hot wallets: **173** (spam bots dropped: 22)
- Known-good CAs loaded: **1134** · watch overlap set: **2568**
- In-watch among hot: **5** · new discoveries: **168**

## Top 20 (on-chain metrics)

| # | address | score | txs | tokens | buys | goodCA | watch | last_block |
|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|
| 1 | `0x046c58b10eef3b19154da71955815f05c0010a8a` | 97.0 | 20 | 4 | 3 | 0 |  | 67395048 |
| 2 | `0xf7a7402a3f96adba63f055ac008ae2eb71fb9dff` | 96.0 | 12 | 3 | 8 | 0 |  | 67395186 |
| 3 | `0x62bbb4913e79992144c1f033777ef842163f5216` | 58.0 | 10 | 2 | 2 | 0 |  | 67395202 |
| 4 | `0x4e97466fbaa8a5fdd4ff1ed509a9bf1a3d5fa357` | 41.0 | 8 | 1 | 1 | 0 |  | 67394902 |
| 5 | `0x49bbf2b70955fb3a106e084d4bfda92d334573d2` | 115.0 | 121 | 0 | 0 | 0 |  | 67395188 |
| 6 | `0xc9b303e7991600615a12bee8a9a5fec528c3b681` | 105.5 | 62 | 0 | 0 | 0 | Y | 67394652 |
| 7 | `0x612d68bb623be91f8a1a52ed96395a750d0e4baa` | 94.8 | 83 | 0 | 0 | 0 |  | 67394780 |
| 8 | `0x5cd826f19e1eb283b31a66f5ea81b752187e4925` | 94.8 | 83 | 0 | 0 | 0 |  | 67394779 |
| 9 | `0x637fa1cc95ac2281b69e9f3f00d9fa35891236a0` | 91.5 | 62 | 0 | 0 | 0 |  | 67394652 |
| 10 | `0xf9d33da08b05e3a78b878ebbedc50485d6e49374` | 50.0 | 16 | 0 | 0 | 0 |  | 67395207 |
| 11 | `0xfb2ded3569d85b2e36fbdb55c4babbda9eb48852` | 44.0 | 13 | 0 | 0 | 0 |  | 67395150 |
| 12 | `0x331d9a049d496385998067abf6cbb6371c8d2466` | 42.0 | 17 | 0 | 0 | 0 |  | 67395209 |
| 13 | `0xca7ded7e4f4ba8ab3b10009236ae6d1b95094589` | 42.0 | 16 | 0 | 0 | 0 |  | 67395161 |
| 14 | `0x6e57d0f76f07a966017b46d319fe93dd84989bed` | 38.0 | 10 | 0 | 0 | 0 | Y | 67395150 |
| 15 | `0x6e052eb81d97ac861973c778a1e77604a08be5b2` | 38.0 | 11 | 0 | 0 | 0 |  | 67395000 |
| 16 | `0x511808449be470efaf4131838b2d4998500a6f72` | 36.0 | 15 | 0 | 0 | 0 |  | 67395190 |
| 17 | `0x344daadde53a81f95bcc46a99d39107a509251a1` | 34.0 | 13 | 0 | 0 | 0 |  | 67395203 |
| 18 | `0x14a8c4598d030f27062af0fae4edc51199fb05e6` | 34.0 | 8 | 0 | 0 | 0 | Y | 67395150 |
| 19 | `0x5512144047c2d55297c8470b93e8da87864617cd` | 34.0 | 15 | 0 | 0 | 0 |  | 67395139 |
| 20 | `0xe209e0047731aa494289e1af9a0d03da19c5ef08` | 34.0 | 13 | 0 | 0 | 0 |  | 67395045 |

## Notes

- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).
- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.
- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.
- LIVE_TRADING=off · no box GMGN.
