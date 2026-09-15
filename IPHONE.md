# iPhoneだけで回す手順（GitHub Actions）

PC不要。Discord通知はスマホアプリで受ける。

## 0. 前提
- GitHubアカウント（無料で可）
- DiscordサーバーでWebhookを作れる権限
- Nansen APIキー（すでにある）

## 1. Discord Webhook（iPhone）
1. Safariで `https://discord.com/channels/@me` を開く
2. 右下メニュー → **デスクトップサイトを表示**（重要。アプリ単体だとWebhookが出せないことが多い）
3. 投稿したいチャンネル → 歯車 → **連携サービス** → **ウェブフック** → 新しいウェブフック
4. URLをコピー（メモアプリに一時保存可。チャットには貼らない）

## 2. GitHubリポジトリを作る（iPhone Safari）
1. Safariで github.com → ログイン → New repository
2. 名前例: `meme-signal-bot` → PublicでもPrivateでも可（Private推奨）→ Create
3. このBot一式（`discord-bot/` の中身 + `rh-wallets/wallets.jsonl`）をリポジトリに上げる
   - 楽なやり方A: PCなしなら、こちらが用意したファイルをGitHubの「Add file → Create new file」で1つずつ貼る（面倒）
   - 楽なやり方B: こちらが zip / 手順付きで上げやすい形にする → あなたは GitHub App か Safari でアップロード
   - 楽なやり方C: リポジトリURLをくれれば、可能な範囲でこっちが中身を整える案内をする

最低必要な構成:
```
bot.py
README.md
.github/workflows/signal.yml
rh-wallets/wallets.jsonl
```

## 3. Secretsを入れる（iPhone Safari）
リポジトリ → Settings → Secrets and variables → Actions → New repository secret

| Name | Value |
|------|--------|
| `NANSEN_API_KEY` | Nansenのキー |
| `DISCORD_WEBHOOK_URL` | DiscordのWebhook URL |

## 4. 動かす
1. Actions タブ → `meme-signal` → **Run workflow**（初回テスト）
2. 成功したら Discord にテスト/シグナルが来る（シグナルが無ければ投稿なし＝正常）
3. 以降は約10分ごとに自動実行（GitHubのcronは遅延することがある）

## 5. 受け取り
Discord iPhoneアプリで通知ONにしておくだけ。

## 注意
- GitHub無料枠のActions分を使う（この頻度なら通常は足りる）
- **super / Grok の容量は使わない**
- 売買はしない（通知のみ）
- 初回は `workflow_dispatch` で手動実行して動作確認
