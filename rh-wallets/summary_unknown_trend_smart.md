# Unknown trend smart wallets — 2026-09-20 14:57 JST

- Trending/seed CAs scanned: **30**
- Known excluded: **11219**
- Unknown candidates (published): **232** (full dump 1145 in `raw/unknown_trend_smart_all.jsonl`)
- Multi-CA wallets: **188**
- Watch candidates (top gate): **25** (not auto-merged into wallets.jsonl)
- RPC calls: **0** (429s=0)
- Elapsed: **53.16s**

## CA sources (top)

- `SI` `0xef82ddc566653699b89c3afe123559a75aaa2976` hot=0.0 src=gecko_trending
- `ZFORGE` `0xb6a906d2d95e862cf4fd43b9e30162aee19eed8d` hot=0.0 src=gecko_trending
- `NVDA` `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` hot=0.0 src=gecko_trending
- `JEV` `0x4d066ab4d924b7b3d01c6ecbfc142efe33aeb7fa` hot=0.0 src=gecko_trending,dex_boosts
- `PONS` `0x39dbed3a2bd333467115de45665cc57f813c4571` hot=0.0 src=gecko_trending
- `URANUS` `0xb911f04a24a9f6234537829290335e623ee71e18` hot=0.0 src=gecko_trending
- `CASHCAT` `0x020bfc650a365f8bb26819deaabf3e21291018b4` hot=0.0 src=gecko_trending
- `VLAD` `0xebd79ceada7a7798ee75727671581dda5a9e88e4` hot=0.0 src=gecko_trending,notify_peak_2x
- `HOODCATS` `0xd17c81cb01d44cc6e822936e8f098960001b47d2` hot=0.0 src=gecko_trending
- `CEST` `0x6b1ca8209a695b27835964c00ac398f5d4f87a22` hot=0.0 src=gecko_trending
- `9e9` `0x385307be7df9b45f55f8be72e0cf8b36466813ee` hot=0.0 src=gecko_trending
- `SHROOM` `0xab093def657f15df31b33922a95e047add645b29` hot=0.0 src=gecko_trending
- `PRISM` `0x20024e485c0b22b42855589700721b28320a7777` hot=0.0 src=gecko_trending
- `WALLET` `0x0339f5459fc690ac85f1782e15782a151b4a9e1b` hot=0.0 src=gecko_trending
- `IF` `0x232cdfc415d10b673845d83dc02ba2eabe7e30d1` hot=0.0 src=gecko_trending
- `SCHIFFY` `0x42afa2124ca5a2b83898e46b2da9a190995b1e18` hot=0.0 src=gecko_trending
- `CPU` `0x8eaa17be69ae9616cede4671e6d837bb89491e18` hot=0.0 src=gecko_trending
- `SUPER` `0x7210afea4a4df412e9275a7153d091ce7612a55d` hot=0.0 src=gecko_trending
- `AD` `0x63ffd4aa844f4befcee1b4606e467d1d209a926b` hot=0.0 src=gecko_trending,dex_boosts
- `ROBIN` `0x11b70d0243baf75e85ce03201a92b5b7c33beb59` hot=0.0 src=gecko_trending

## Top 10 unknown addresses

1. `0x6a41697a5c1d725ba787dfb3903e1e0338c9b131` score=180.167 n_cas=10 buys=25 vol=$633.4 syms=VLAD,HOODCATS,9e9,AD,HEDGE,BEORN,ASKR,HOOKR
2. `0x08d0c5049e4bc42102f22e6588c96374b923057c` score=137.733 n_cas=7 buys=14 vol=$1866.7 syms=CEST,PRISM,WALLET,CPU,AD,CEST,HOOKR
3. `0x6abea4998f9ef34ee1cda5715ccca0e4d4efb730` score=121.065 n_cas=6 buys=14 vol=$1333.0 syms=NVDA,CASHCAT,SHROOM,ROBIN,DELTA,HOOKR
4. `0x0df6c565a49f8fcc02251836236f980d304a3930` score=110.811 n_cas=6 buys=7 vol=$122.2 syms=SI,JEV,SHROOM,AD,ASKR,DELTA
5. `0x359b288eb3ab1fb4553b5424b96d62f33e6eb359` score=106.906 n_cas=5 buys=10 vol=$1781.2 syms=JEV,CEST,9e9,SUPER,CEST
6. `0x1b36374d5a26c45726632162188f7162dcf39639` score=98.116 n_cas=5 buys=10 vol=$23.2 syms=SHROOM,IF,CPU,SUPER,BEORN
7. `0x457c31f85b0c5760564676939e4fafb2e454b108` score=96.729 n_cas=5 buys=5 vol=$345.9 syms=CASHCAT,SHROOM,ROBIN,DELTA,HOOKR
8. `0x05854d0c70d2f696fb3d9c0863100956b795e2ff` score=96.547 n_cas=5 buys=7 vol=$69.3 syms=WALLET,IF,DELTA,HOOKR,NOTE
9. `0x4e23424b3c635ec2cad284787467b1700517421d` score=83.923 n_cas=4 buys=8 vol=$624.6 syms=JEV,SHROOM,CEST,musebook
10. `0x13c2e85715d8f6fcd48518eccd74ee1dc272ecb8` score=82.663 n_cas=4 buys=4 vol=$852.7 syms=SI,CEST,PRISM,AD

## Notes

- Buyers from Gecko pool trades (`kind=buy`) via jina; optional RH RPC Transfer logs.
- Excludes all rh-wallets / fomo / arc / scout seed addresses + USDG/WETH stables.
- Hubs that hit ≥40% of scanned CAs in one run are dropped (router/bot-like).
- `watch_candidates_unknown.jsonl` is staging only — do **not** dump into `wallets.jsonl` without a quality gate.

