# 独立 Discord シグナルBot（super容量ゼロ）

このフォルダを **VPS / 自宅常時PC / GitHub Actions** などに置き、そこで定期実行する。
Grok Bot のルーチンには載せない（載せると容量を食う）。

## 何をするか
- Nansen Smart Money DEX trades（既定: `robinhood`）を定期取得
- `wallets.jsonl` の監視財布が **同一コントラクトを15分以内に2本以上** 買ったら Discord に投稿
- **売買しない**（通知のみ）。紙検証・実弾は別問題

## 投稿前ゲート（自動）
- **監視財布**: `pass_pnl` または実現損益>0（環境変数 `WATCH_MIN_REALIZED_USD`、既定0）。空になったら全件にフォールバックし警告
- **安全チェック**: DexScreener で流動性/時価（なければFDV）≥30%。時価が取れなければ投稿しない。GoPlus でハニーポット・売却不可・高税があれば見送り（未対応チェーンは DexScreener のみで続行）
- **同一コントラクト冷却**: 既定6時間（`COOLDOWN_SECONDS=21600`）は再投稿しない（紙ログには記録）
- **ATH追いフィルタは未実装**（意図的に入れない）

## 通知の強さ（日本語）
- 3人以上かつ合計約$500以上 →「かなり強い」
- 3人以上 →「やや強い」
- それ以外 →「買いが重なった」
- Discord は単一埋め込み＋DexScreener / エクスプローラーリンク

## 紙ログ
- 候補ごとに `paper_log.jsonl` へ追記（投稿 / 見送り理由）
- Actions では `state.json` と `paper_log.jsonl` をキャッシュ

## Nansenクレジット節約
- Actions cron は **20分ごと**（`*/20 * * * *`）
- 既定ページ数 **2**（`--pages 2`）
- ページ間は短い待ちを入れる

## Discord Webhook の作り方
1. Discord → 投稿チャンネル → チャンネル設定
2. 連携サービス → ウェブフック → 新しいウェブフック
3. URLをコピー → 実行マシンの `.env` の `DISCORD_WEBHOOK_URL` に入れる
4. URLはチャットに貼らない

## セットアップ（実行マシン）
```bash
cd discord-bot
cp .env.example .env
# .env を編集: NANSEN_API_KEY / DISCORD_WEBHOOK_URL
# wallets.jsonl を同じマシンに置く（デフォルト rh-wallets/wallets.jsonl）
python3 bot.py
```

## テスト投稿だけ
```bash
python3 bot.py --test-webhook
```

## Arc について
- `CHAIN=arc` は任意。Nansen が Arc を拒否した場合は **例外で落とさずスキップ** し、紙ログに残す。Robinhood 経路には影響しない。

## 注意
- Nansenクレジットを消費する。ページ数・頻度を広げすぎない
- シークレットをログに出さない
