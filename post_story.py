#!/usr/bin/env python3
"""フォア＆モア久留米の Instagram ストーリーを自動投稿する。

毎朝6時（日曜休み）に、sets.json の中からランダムに2セット（各4枚）を選び、
1→4枚目の順でストーリーに投稿する。

- 直近6投稿日に出したセットは選ばない（同じものが続かないように）
- expires を過ぎたセット（10月キャンペーンなど）は選ばない
- 1日1回だけ出す。投稿済みかどうかは history.json で判定する

鍵（トークン）の期限が残り7日・3日・1日になった朝と、投稿に失敗したときは
LINE（saki's AI →「わたしのしごと」グループ）にお知らせを送る。

必要な環境変数（GitHub Secrets）:
    IG_ACCESS_TOKEN   instagram_content_publish 権限つきのトークン
    IG_USER_ID        @foreandmore_kurume の Instagram ユーザーID
    LINE_CHANNEL_ACCESS_TOKEN / LINE_GROUP_ID   お知らせ用（なくても投稿は動く）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
# 店舗ごとの設定。--account で切り替える
ACCOUNTS = {
    "kurume": {"sets": "sets.json", "history": "history.json", "images": "images/",
               "username": "foreandmore_kurume", "user_id_env": "IG_USER_ID", "label": "久留米"},
    "chikugo": {"sets": "sets_chikugo.json", "history": "history_chikugo.json", "images": "images_chikugo/",
                "username": "foreandmorechikugo", "user_id_env": "IG_USER_ID_CHIKUGO", "label": "筑後"},
}
JST = dt.timezone(dt.timedelta(hours=9))
API = "https://graph.facebook.com/v22.0"
# 画像は公開リポジトリの raw URL から Instagram に取り込ませる
RAW_ROOT = "https://raw.githubusercontent.com/tanaka-create/foreandmore-ig-story/main/"

POST_HOUR = 6          # 6:00 JST に出す
SETS_PER_DAY = 2
AVOID_DAYS = 6         # 直近6投稿日に出したセットは避ける
LATEST_HOUR = 12       # cron が大幅に遅れてもこの時刻を過ぎたら出さない


def now() -> dt.datetime:
    return dt.datetime.now(JST)


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def api(method: str, path: str, token: str, **params) -> dict:
    params["access_token"] = token
    data = urlencode(params).encode()
    url = f"{API}/{path}"
    req = Request(url if method == "POST" else f"{url}?{data.decode()}",
                  data=data if method == "POST" else None, method=method)
    try:
        with urlopen(req, timeout=60) as r:
            return json.load(r)
    except HTTPError as e:
        msg = json.load(e).get("error", {}).get("message", str(e))
        raise RuntimeError(f"{path}: {msg}") from None


NOTIFY_DAYS = (7, 3, 1)  # 期限の何日前にお知らせするか

RENEW_HOWTO = (
    "【更新のしかた】\n"
    "ビジネス設定 → システムユーザー「test」→ トークンを生成\n"
    "→ インスタ分析用アプリ → 60日間 → 権限5件（instagram_content_publish 入り）\n"
    "→ GitHub の foreandmore-ig-story の Secrets「IG_ACCESS_TOKEN」に上書き登録\n"
    "わからなければClaudeに「ストーリーの鍵を更新したい」と言えばOKです"
)


def line_notify(text: str) -> None:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    to = os.environ.get("LINE_GROUP_ID", "")
    if not token or not to:
        print("[WARN] LINE の設定がないので通知を送れない")
        return
    body = json.dumps({"to": to, "messages": [{"type": "text", "text": text}]}).encode()
    req = Request("https://api.line.me/v2/bot/message/push", data=body, method="POST",
                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        urlopen(req, timeout=30).read()
        print("[INFO] LINE に通知した")
    except HTTPError as e:
        print(f"[WARN] LINE 通知に失敗: {e.code}")


def token_days_left(token: str) -> int | None:
    """トークンの残り日数。無期限なら None。"""
    info = api("GET", "debug_token", token, input_token=token).get("data", {})
    exp = info.get("data_access_expires_at") if not info.get("expires_at") else info.get("expires_at")
    if not exp:
        return None
    left = dt.datetime.fromtimestamp(exp, JST) - now()
    return left.days


def check_expiry(token: str, force_notify: bool = False) -> int | None:
    try:
        left = token_days_left(token)
    except Exception as exc:
        print(f"[WARN] 期限の確認に失敗: {exc}")
        return None
    print(f"[INFO] 鍵の残り日数: {left if left is not None else '無期限'}")
    if left is not None and (left in NOTIFY_DAYS or left <= 0 or force_notify):
        when = "今日で切れます" if left <= 0 else f"あと{left}日で切れます"
        line_notify(f"📸 フォア＆モアのストーリー自動投稿\nInstagramの鍵（トークン）が{when}。\n切れると毎朝のストーリーが止まるので、更新をお願いします🙏\n\n{RENEW_HOWTO}")
    return left


def pick(sets: dict, history: list, today: dt.date) -> list[str]:
    recent = {k for h in history[-AVOID_DAYS:] for k in h["sets"]}
    alive = [k for k, v in sets.items()
             if not v.get("expires") or today <= dt.date.fromisoformat(v["expires"])]
    pool = [k for k in alive if k not in recent]
    if len(pool) < SETS_PER_DAY:
        pool = alive
    return random.sample(pool, SETS_PER_DAY)


def post_image(token: str, user_id: str, url: str) -> str:
    c = api("POST", f"{user_id}/media", token, image_url=url, media_type="STORIES")
    cid = c["id"]
    for _ in range(30):
        st = api("GET", cid, token, fields="status_code").get("status_code")
        if st == "FINISHED":
            break
        if st == "ERROR":
            raise RuntimeError(f"画像の取り込みに失敗: {url}")
        time.sleep(3)
    return api("POST", f"{user_id}/media_publish", token, creation_id=cid)["id"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="選ぶだけで投稿しない")
    ap.add_argument("--check", action="store_true", help="鍵と投稿権限の確認だけ行う（投稿しない）")
    ap.add_argument("--account", default="kurume", choices=list(ACCOUNTS), help="どの店舗に出すか")
    ap.add_argument("--force", action="store_true", help="時刻・曜日・投稿済みの判定を無視する（手動テスト用）")
    args = ap.parse_args()
    acc = ACCOUNTS[args.account]
    global SETS, HISTORY, RAW_BASE, EXPECTED_USERNAME
    SETS, HISTORY = BASE / acc["sets"], BASE / acc["history"]
    RAW_BASE = RAW_ROOT + acc["images"]
    EXPECTED_USERNAME = acc["username"]
    print(f"[INFO] 店舗: {acc['label']}（@{EXPECTED_USERNAME}）")

    if args.check:
        token = os.environ.get("IG_ACCESS_TOKEN", "")
        user_id = os.environ.get(acc["user_id_env"], "")
        me = api("GET", user_id, token, fields="username")
        print("[CHECK] 接続先:", me.get("username"))
        lim = api("GET", f"{user_id}/content_publishing_limit", token, fields="quota_usage,config")
        print("[CHECK] 投稿権限OK・24時間の投稿上限:", lim.get("data", [{}])[0])
        check_expiry(token, force_notify=os.environ.get("NOTIFY_TEST") == "1")
        return 0 if me.get("username") == EXPECTED_USERNAME else 1

    t = now()
    today = t.date()
    sets = load(SETS, {})
    history = load(HISTORY, [])

    if not args.force:
        if t.weekday() == 6:
            print("[SKIP] 日曜は投稿しない")
            return 0
        if any(h["date"] == today.isoformat() for h in history):
            print("[SKIP] 今日はもう投稿済み")
            return 0
        if t.hour >= LATEST_HOUR:
            print(f"[SKIP] {LATEST_HOUR}時を過ぎたので今日は出さない")
            return 0
        start = t.replace(hour=POST_HOUR, minute=0, second=0, microsecond=0)
        wait = (start - t).total_seconds()
        if wait > 25 * 60:
            print("[SKIP] まだ早い")
            return 0
        if wait > 0 and not args.dry_run:
            print(f"[INFO] 6:00まで {int(wait)}秒待つ")
            time.sleep(wait)

    chosen = pick(sets, history, today)
    print("[INFO] 選んだセット:", ", ".join(f"{k}（{sets[k]['title']}）" for k in chosen))
    if args.dry_run:
        for k in chosen:
            for f in sets[k]["files"]:
                print("  ", RAW_BASE + f)
        return 0

    token = os.environ.get("IG_ACCESS_TOKEN", "")
    user_id = os.environ.get(acc["user_id_env"], "")
    if not token or not user_id:
        print(f"[ERROR] IG_ACCESS_TOKEN / {acc['user_id_env']} が未設定", file=sys.stderr)
        return 1
    if args.account == "kurume":  # 鍵は両店舗共通なので、期限のお知らせは久留米の回だけで送る
        check_expiry(token)
    try:
        me = api("GET", user_id, token, fields="username")
    except Exception as exc:
        line_notify(f"⚠️ フォア＆モア{acc['label']}店のストーリー自動投稿が止まりました\nInstagramにつながりませんでした（鍵の期限切れの可能性があります）。\n{exc}\n\n{RENEW_HOWTO}")
        raise
    if me.get("username") != EXPECTED_USERNAME:
        print(f"[ERROR] 接続先が違う: {me.get('username')}", file=sys.stderr)
        return 1

    posted = []
    try:
        for k in chosen:
            for f in sets[k]["files"]:
                mid = post_image(token, user_id, RAW_BASE + f)
                posted.append({"file": f, "id": mid})
                print(f"[OK] {f} → {mid}")
                time.sleep(5)
    except Exception as exc:
        line_notify(f"⚠️ フォア＆モア{acc['label']}店のストーリー自動投稿で失敗しました\n{len(posted)}枚出したところで止まっています。\n{exc}\n\n鍵の期限切れなら↓\n{RENEW_HOWTO}")
        raise
    finally:
        if posted:
            history.append({"date": today.isoformat(), "time": now().strftime("%H:%M"),
                            "sets": chosen, "posted": len(posted)})
            HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
