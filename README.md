# 独立 Discord シグナルBot（super容量ゼロ）

このフォルダを **VPS / 自宅常時PC / GitHub Actions** などに置き、そこで定期実行する。
Grok Bot のルーチンには載せない（載せると容量を食う）。紙トレード更新も **Actions のみ**。

## 何をするか
- **主ソース**: GMGN `track smartmoney`（`gmgn-cli track smartmoney --chain robinhood --side buy --limit … --raw`）
- **監視リスト厳格**: フィルタ済み監視財布が **同一CAを15分以内に2本以上** 買ったときだけ投稿（既定）
- `ALLOW_GMGN_CLUSTER=0`（既定）で監視リスト外クラスタ投稿は無効。必要なら `1` で再有効化
- GMGN失敗時はオンチェーン探索を best-effort（偽取引は作らない）
- **倍率フォローアップ**: 1.5x / 2x / 3x / 5x 到達を各1回（プレーン日本語）
- **紙トレード**（仮想 **$300**）: 投稿時に仮想ポジション。FOUNDATION準拠
  - 同時1本 / サイズ20%（n≥3は30%）/ +100%半分 / −40%ストップ / 週最大5 / 連敗3で週終了
  - `paper_book.jsonl` + `paper_summary.md`（エクイティ曲線）を Actions cache / artifact で永続
- **実弾禁止**: `LIVE_TRADING=0`（有効化してもブロック）
- **ATH追いフィルタは未実装**（意図的）

## チェーン
- 既定: `CHAIN=robinhood` + `rh-wallets/wallets.jsonl`
- 任意: `CHAIN=arc` + `arc-wallets/wallets.jsonl`（RHを壊さない）

## Nansen
- 定期ジョブでは dex-trades を呼ばない（`NANSEN_FOR_TRADES=0`）
- 週次: `refresh-wallets.yml` → `python3 bot.py --refresh-wallets`（artifact）

## 投稿前ゲート
- 監視財布: `pass_pnl` または実現損益>0
- 安全: DexScreener **流動性/時価 ≥30%**。時価なしは見送り。GoPlus未対応はスキップ
- 安全見送りは Discord に短い通知（1実行あたり最大3件）
- 冷却: 既定 **2時間**（`COOLDOWN_SECONDS=7200`）

## GitHub Secrets
| Secret | 用途 |
|--------|------|
| `GMGN_API_KEY` | 定期ジョブ必須 |
| `DISCORD_WEBHOOK_URL` | 必須 |
| `NANSEN_API_KEY` | `--refresh-wallets` 用。定期pollのenvからは外す |

秘密鍵・実弾キーは Actions に置かない。

## セットアップ
```bash
cd discord-bot
cp .env.example .env
# GMGN_API_KEY / DISCORD_WEBHOOK_URL / PAPER_BANKROLL_USD=300
npm install -g gmgn-cli
python3 load_secrets.py   # box 上
python3 bot.py --per-page 100
python3 bot.py --paper-summary
```

## ワークフロー
- `signal.yml`: `*/5` 本ポーリング + 紙マーク更新
- `paper-summary.yml`: 週次（Sat 00:00 UTC ≈ Sun 09:00 JST）`0 0 * * 6`
- `refresh-wallets.yml`: 週次 Nansen 財布更新 → artifact

## 注意
- シークレットをログに出さない
- Public リポ。Actions 無料枠のため cron は `*/5`
