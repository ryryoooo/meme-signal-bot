# 独立 Discord シグナルBot（super容量ゼロ）

このフォルダを **VPS / 自宅常時PC / GitHub Actions** などに置き、そこで定期実行する。
Grok Bot のルーチンには載せない（載せると容量を食う）。紙トレード更新も **Actions のみ**。

## 何をするか
- **主ソース**: GMGN `track smartmoney` ＋ **FOMOリーダーの買い**（同じ15分クラスタ）
- **監視リスト厳格**: フィルタ済み監視財布が **同一CAを15分以内に2本以上** 買ったときだけ投稿（既定）
- `ALLOW_GMGN_CLUSTER=0`（既定）で監視リスト外クラスタ投稿は無効。必要なら `1` で再有効化
- GMGN失敗時はオンチェーン探索を best-effort（偽取引は作らない）
- **倍率フォローアップ**: 1.5x / 2x / 3x / 5x 到達を各1回（プレーン日本語）
- **紙トレード**（仮想 **$300**）: 投稿時に仮想ポジション。FOUNDATION準拠。 Discordは `DISCORD_PAPER_WEBHOOK_URL`（紙）と `DISCORD_WEBHOOK_URL`（シグナル）に分離
  - 同時1本 / サイズ20%（n≥3は30%）/ +100%半分 / −40%ストップ / 週最大5 / 連敗3で週終了
  - `paper_book.jsonl` + `paper_summary.md`（エクイティ曲線）を Actions cache / artifact で永続
- **実弾禁止**: `LIVE_TRADING=0`（有効化してもブロック）
- **ATH追いフィルタは未実装**（意図的）

## チェーン
- 既定: `CHAIN=robinhood` + `rh-wallets/wallets.jsonl`
- Arc: `CHAIN=arc` + `arc-wallets/wallets.jsonl`（RHを壊さない）
  - **Arc 公開メインネット開始: 2026-09-16**（JST）
  - 定期ジョブ `signal-arc.yml`（`meme-signal-arc`）が RH と並列で `*/5` 稼働
  - 状態は分離: `state-arc.json` / `paper_*_arc.*`（紙 $300 も RH と混ぜない）
  - シグナルWebhookは `DISCORD_ARC_WEBHOOK_URL` 優先（未設定時は `DISCORD_WEBHOOK_URL`）

## Nansen
- 定期ジョブでは dex-trades を呼ばない（`NANSEN_FOR_TRADES=0`）
- 週次: `refresh-wallets.yml` → `python3 bot.py --refresh-wallets`（artifact）

## X / @xbtscout
- RHジョブ内で **30分おき** に nitter から新CAを取る（X公式は403）
- **新しく出たCAだけ** GMGN `smart_degen` を1本。既存261の再スキャンはしない
- 取れた財布は監視リストへ `xbtscout_gmgn`。キャッシュで永続

## 投稿前ゲート
- 監視財布: 実現PnL>0。勝率データがある場合は <40%かつ10戦以上を除外（FOMO専任は週次PnL）
- 安全: DexScreener **流動性/時価 ≥30%**。GoPlusで **LPロックorバーン**（未ロック見送り、LP上位が30%以上も見送り）。GoPlus未対応チェーンは契約/LP検査スキップ
- 監視脱落: 実現PnL≤0、または勝率<40%かつ10戦以上。FOMO専任は勝率なし→週次リーダーPnLで入れ直し
- 安全見送りは Discord に短い通知（1実行あたり最大3件）
- 冷却: 既定 **2時間**（`COOLDOWN_SECONDS=7200`）

## GitHub Secrets
| Secret | 用途 |
|--------|------|
| `GMGN_API_KEY` | 定期ジョブ必須 |
| `DISCORD_WEBHOOK_URL` | シグナル／安全見送り必須（RH。Arc未設定時のフォールバック） |
| `DISCORD_ARC_WEBHOOK_URL` | Arcシグナル専用（推奨。未設定時は上にフォールバック） |
| `DISCORD_PAPER_WEBHOOK_URL` | 紙トレード専用（未設定時はシグナル側にフォールバック） |
| `DISCORD_ARC_PAPER` | Arc紙トレード専用（任意。未設定時は紙Webhookへ） |
| `NANSEN_API_KEY` | `--refresh-wallets` 用。定期pollのenvからは外す |
| `FOMO_API_KEY` | RHジョブ。`/v2/alerts` を25分以上間隔。未設定ならGMGNのみ |

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
- `signal.yml`: RH `*/5` ポーリング + 紙マーク更新
- `signal-arc.yml`: Arc `*/5` ポーリング + 分離紙状態（メインネット 2026-09-16 開始想定）
- `paper-summary.yml`: 週次（Sat 00:00 UTC ≈ Sun 09:00 JST）`0 0 * * 6`（RH）
- `refresh-wallets.yml`: 週次 Nansen 財布更新 → artifact

## FOMO
- リーダー（PnL+）のEVMを RH 監視リストに合流（同一アドレスは二重計上しない）
- 買い1本でも、FOMOリーダーの**保有が2人以上**なら同じクラスタ扱い（4時間に1回、250クレジット）
- 買いフィード `GET /v2/alerts?type=buy&chain=robinhood` を **25分以上** 間隔（1回125クレジット。無料枠 250,000/月）
- ハンドルがリーダーに無い買い（フォロワーのノイズ）は捨てる
- Arc は FOMO 対象外（チェーン非対応）
- WebSocket は GitHub Actions では張れないので REST のみ

## 注意
- シークレットをログに出さない
- Public リポ。Actions 無料枠のため cron は `*/5`
