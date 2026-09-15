# 独立 Discord シグナルBot（super容量ゼロ）

このフォルダを **VPS / 自宅常時PC / Railway** などに置き、そこで常駐させる。
Grok Bot のルーチンには載せない（載せると容量を食う）。

## 何をするか
- Nansen Smart Money DEX trades（`robinhood`）を定期取得
- `wallets.jsonl` の監視財布が **同一CAを15分以内に2本以上** 買ったら Discord に投稿
- **売買しない**（通知のみ）。紙検証・実弾は別問題

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
# wallets.jsonl を同じマシンに置く（デフォルト ../rh-wallets/wallets.jsonl）
python3 bot.py
```

常駐例（systemd / screen / tmux）:
```bash
# tmux
tmux new -s meme-bot
python3 bot.py
# Ctrl-b d でデタッチ
```

## テスト投稿だけ
```bash
python3 bot.py --test-webhook
```

## 注意
- Nansenクレジットを消費する。`POLL_SECONDS` を広げすぎない／狭くしすぎない
- セーフティ（honeypot / liq30%）はまだスタブ。通知に「safety=unchecked」と出す
- Arc は Nansen非対応のため、このBotの第一版は RH のみ
