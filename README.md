# フォア＆モア久留米 Instagramストーリー自動投稿

- 毎朝6:00（日曜休み）に `sets.json` からランダムで1セット（4枚）をストーリーに投稿
- 画像は `images/`（Vault `MyWork/フォア＆モア久留米/ストーリー/` のPNGをJPEG化したもの）
- 投稿記録は `history.json`
- Secrets: `IG_ACCESS_TOKEN`（instagram_content_publish 権限つき）・`IG_USER_ID`

## 筑後店（2026-10-01追加）
- 黒背景版15セット `images_chikugo/`・`sets_chikugo.json`・記録 `history_chikugo.json`
- 定時投稿はリポジトリ変数 `CHIKUGO_ENABLED=true` で有効化（筑後のInstagramにシステムユーザーの権限を付けてから）
- Secrets: `IG_USER_ID_CHIKUGO`
