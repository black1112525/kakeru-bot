import os
import sys
import json
import time
import psycopg2
import random
from datetime import datetime, timezone, timedelta
from flask import Flask, request, abort
from collections import defaultdict
import requests

# LINE SDK
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# Flaskアプリ初期化
app = Flask(__name__)

# 環境変数
DATABASE_URL = os.getenv("DATABASE_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CRON_TOKEN = os.getenv("CRON_TOKEN")

if not all([DATABASE_URL, LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET]):
    print("❌ 環境変数が不足しています。Renderの設定を確認してください。")
    sys.exit(1)

# LINE設定
handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# レート制限
last_hit = defaultdict(lambda: 0)
def rate_limited(uid, interval=3):
    now = time.time()
    if now - last_hit[uid] < interval:
        return True
    last_hit[uid] = now
    return False

# 安全返信
def safe_reply(token, text):
    try:
        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)
            line_api.reply_message(
                ReplyMessageRequest(
                    reply_token=token,
                    messages=[TextMessage(text=text)]
                )
            )
    except Exception as e:
        print(f"[LINE送信エラー] {e}")

# --- Webhook受信 ---
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# === 恋愛おみくじ ===
def get_love_fortune():
    fortunes = [
        "💘 大吉：運命の出会いが訪れるかも！積極的に行動してみよう！",
        "💖 中吉：笑顔が恋を引き寄せる日。素直な気持ちを伝えてみて！",
        "💞 小吉：焦らず一歩ずつ。相手のペースを大切にしてね。",
        "💔 凶：今日は自分を癒す日。無理せずリラックスしよう。",
        "💗 吉：連絡するなら夜がチャンス！自然体が一番魅力的。"
    ]
    return random.choice(fortunes)

# === 会話メイン ===
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = (event.message.text or "").strip()

    if rate_limited(user_id):
        return
    if not text or len(text) > 800:
        safe_reply(event.reply_token, "メッセージは1文字以上800文字以内で送ってね！")
        return

    # --- DB接続 ---
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        # ユーザーデータ保存テーブル
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_data (
                user_id TEXT PRIMARY KEY,
                talk_count INTEGER DEFAULT 0,
                last_talk TIMESTAMP
            );
        """)
        # 会話履歴テーブル
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                user_id TEXT,
                message TEXT,
                timestamp TIMESTAMP
            );
        """)
        # 話した回数
        cur.execute("SELECT talk_count FROM user_data WHERE user_id=%s;", (user_id,))
        result = cur.fetchone()
        if result:
            talk_count = result[0] + 1
            cur.execute(
                "UPDATE user_data SET talk_count=%s, last_talk=%s WHERE user_id=%s;",
                (talk_count, datetime.now(timezone.utc), user_id)
            )
        else:
            talk_count = 1
            cur.execute(
                "INSERT INTO user_data (user_id, talk_count, last_talk) VALUES (%s,%s,%s);",
                (user_id, talk_count, datetime.now(timezone.utc))
            )

        # 今回のメッセージを保存
        cur.execute(
            "INSERT INTO chat_history (user_id, message, timestamp) VALUES (%s, %s, %s);",
            (user_id, text, datetime.now(timezone.utc))
        )
        # 最新3件だけ残す
        cur.execute("""
            DELETE FROM chat_history
            WHERE user_id=%s AND timestamp NOT IN (
                SELECT timestamp FROM chat_history
                WHERE user_id=%s ORDER BY timestamp DESC LIMIT 3
            );
        """, (user_id, user_id))

        conn.commit()
    conn.close()

    # 会話レベル
    if talk_count <= 3:
        level = 1
    elif talk_count <= 10:
        level = 2
    else:
        level = 3

    # 時間帯
    hour = datetime.now(timezone(timedelta(hours=9))).hour
    if hour < 10:
        greet = "おはようございます☀️"
    elif hour < 18:
        greet = "こんにちは🌸"
    else:
        greet = "こんばんは🌙"

    # おみくじ
    if "おみくじ" in text or "占い" in text:
        reply_text = f"{greet}\n今日の恋愛運は…\n\n{get_love_fortune()}"
        safe_reply(event.reply_token, reply_text)
        return

    # --- 過去の話を思い出す ---
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT message FROM chat_history WHERE user_id=%s ORDER BY timestamp DESC LIMIT 2;", (user_id,))
        past = [r[0] for r in cur.fetchall()]
    conn.close()

    recall_text = ""
    if talk_count > 5 and past:
        last_topic = past[-1]
        recall_text = f"そういえば前に『{last_topic[:20]}…』って話してましたね。その後どうなりましたか？\n\n"

    # --- レベル別応答 ---
    if level == 1:
        reply_text = f"{greet}\nはじめまして。メッセージありがとうございます。\n恋愛や人間関係のこと、どんなことでも話してみてくださいね。"
    elif level == 2:
        reply_text = f"{recall_text}なるほど…。少し気持ちが整理できたかもしれませんね。もう少し詳しく話してもらえますか？"
    else:
        reply_text = f"{recall_text}そっかぁ。気になるねぇ😌　俺でよければもう少し聞かせて？"

    safe_reply(event.reply_token, reply_text)

# --- Render確認 ---
@app.route("/")
def home():
    return "KakeruBot is running 🚀"

# --- おみくじ自動配信（Cron対応） ---
@app.route("/cron/daily-uraniai", methods=["POST"])
def cron_daily():
    if request.headers.get("X-Cron-Token") != CRON_TOKEN:
        abort(401)

    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT user_id FROM user_data;")
        users = cur.fetchall()
    conn.close()

    if not users:
        return "No users"

    fortune = get_love_fortune()
    push_message = f"🌅おはようございます！\n今日の恋愛運は…\n\n{fortune}"

    for user in users:
        user_id = user[0]
        try:
            requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers={
                    "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={
                    "to": user_id,
                    "messages": [{"type": "text", "text": push_message}],
                },
            )
        except Exception as e:
            print(f"[Cron送信エラー] {e}")

    return "OK"

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
