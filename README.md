# 独立 Discord シグナルBot（super容量ゼロ）

このフォルダを **VPS / 自宅常時PC / GitHub Actions** などに置き、そこで定期実行する。
Grok Bot のルーチンには載せない（載せると容量を食う）。

## 何をするか
- **主ソース**: GMGN `track smartmoney`（`gmgn-cli track smartmoney --chain robinhood --side buy --limit … --raw`）
- 監視リスト（`rh-wallets/wallets.jsonl` の `pass_pnl` / 実現損益>0）と交差した買いが **同一コントラクトを15分以内に2本以上** なら Discord に投稿
- 監視リスト交差が少なすぎる場合は、GMGNスマートマネークラスタ単体も許可（埋め込みに **「GMGNスマートマネー（監視リスト外含む）」** と明記）
- GMGN が失敗（`AUTH_KEY_INVALID` / 401 / レート制限）したらオンチェーン探索を best-effort で試す（DexScreenerは財布取引を列挙できない。RH Blockscout は Cloudflare / Pro API 鍵が必要なことが多く、取れなければ新シグナルは出さない）。**偽の取引は作らない**
- **倍率フォローアップ**: 投稿時に `state.json` へ価格・時価・流動性を保存。24時間以内のアラートを DexScreener で再取得し、1.5x/2x/3x/5x 到達（または30分クールダウン付きの変動）で「さっきの通知から ○.○倍」を追記
- **売買しない**（通知のみ）

## Nansen の使い方（クレジット節約）
- **定期ジョブでは Nansen dex-trades を呼ばない**（既定 `NANSEN_FOR_TRADES=0`）
- 財布リストのまれな更新だけ: `python3 bot.py --refresh-wallets`（または手元の `rh-wallets/collect_*.py`）
- Actions cron は引き続き **20分ごと**（`*/20 * * * *`）。Nansenページ数の概念は定期実行では不要

## 投稿前ゲート
- **監視財布**: `pass_pnl` または実現損益>0（`WATCH_MIN_REALIZED_USD`、既定0）
- **安全チェック**: DexScreener で **流動性/時価 ≥30%**（なければFDV）。時価が取れなければ投稿しない。GoPlus は未対応チェーンならスキップ
- 埋め込みに **時価総額 / 流動性 / liq/mcap / DexScreener リンク** を出す
- **同一コントラクト冷却**: 既定6時間（`COOLDOWN_SECONDS=21600`）は新規シグナルを再投稿しない
- **ATH追いフィルタは未実装**（意図的）

## GitHub Secrets
| Secret | 用途 |
|--------|------|
| `GMGN_API_KEY` | 定期ジョブ必須。`gh secret set GMGN_API_KEY`（値はログに出さない） |
| `DISCORD_WEBHOOK_URL` | 必須 |
| `NANSEN_API_KEY` | `--refresh-wallets` 用に残してよい。定期ジョブの env からは外す |

ローカル / box では `load_secrets.py` が `box-secrets.json` の `desktop.GMGN_API_KEY` を読み、`~/.config/gmgn/.env` に書く（キーは印刷しない）。

## セットアップ
```bash
cd discord-bot
cp .env.example .env
# GMGN_API_KEY / DISCORD_WEBHOOK_URL を設定
npm install -g gmgn-cli   # または既存バイナリ
python3 load_secrets.py   # box 上なら ~/.config/gmgn/.env を用意
python3 bot.py --per-page 100
```

## テスト
```bash
python3 bot.py --test-webhook
```

## 紙ログ
- `paper_log.jsonl` / `state.json`（Actions は cache）

## 注意
- シークレットをログに出さない
- GMGN キーが無効なときはソフト終了し、既存アラートの倍率更新だけ続行する

## 運用メモ
- リポジトリはPublic（Actions無料枠のため）
- チェック間隔: 約5分（GitHub cronは多少遅延しうる）
- 秘密情報はGitHub Secretsのみ（コードにキーは無い）
