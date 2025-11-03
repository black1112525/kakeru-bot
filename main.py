import os
import sys
import json
import time
import datetime
import requests
from collections import defaultdict
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import psycopg2
from psycopg2.extras import Json

# === Flask設定 ===
app = Flask(__name__)

# === 環境変数 ===
DATABASE_URL = os.getenv("DATABASE_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CRON_TOKEN = os.getenv("CRON_TOKEN")

# === 環境変数チェック ===
if not all([DATABASE_URL, LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, OPENAI_API_KEY, CRON_TOKEN]):
    print("❌ 必須環境変数が設定されていません。Renderの環境変数設定を確認してください。")
    sys.exit(1)

handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# === データベース初期化 ===
def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_data (
            user_id VARCHAR(255) PRIMARY KEY,
            history JSONB,
            last_updated TIMESTAMPTZ
        );
        """)
    conn.commit()
    conn.close()

init_db()

# === カケル人格設定 ===
KAKERU_SYSTEM = """
あなたの名前は「カケル」。男性向け恋愛カウンセラー兼親友AIです。
【ルール】
- 一人称は「俺」。丁寧すぎず、自然体で。
- 相手を否定せず、共感を重視する。
- 恋愛相談が中心。性的・暴力的・個人情報系の話題は禁止。
- 医療や法律相談には専門家を案内。
- 返信は800文字以内、最後に「今日の恋愛運」を一言添える。
"""

# === スパム防止 ===
last_hit = defaultdict(float)
def rate_limited(uid, interval=2.0):
    now = time.time()
    if now - last_hit[uid] < interval:
        return True
    last_hit[uid] = now
    return False

# === Webhook受信 ===
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# === メッセージ処理 ===
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = (event.message.text or "").strip()

    # スパム防止
    if rate_limited(user_id):
        safe_reply(event.reply_token, "少し間をあけて話そう！")
        return

    # 空メッセージ対応
    if not text:
        safe_reply(event.reply_token, "メッセージが空だよ！")
        return
    if len(text) > 800:
        text = text[:800] + "…（長文省略）"

    # DB接続
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:

        # 履歴削除コマンド
        if text in ("/clear", "履歴リセット"):
            cur.execute("DELETE FROM user_data WHERE user_id = %s;", (user_id,))
            conn.commit()
            conn.close()
            safe_reply(event.reply_token, "OK！会話履歴をリセットしたよ。")
            return

        # モデレーション
        if is_flagged(text):
            conn.close()
            safe_reply(event.reply_token, "ごめん、安全のためその話題には答えられないんだ。")
            return

        # 履歴取得
        cur.execute("SELECT history, last_updated FROM user_data WHERE user_id = %s;", (user_id,))
        result = cur.fetchone()
        history = []

        if result:
            if datetime.datetime.now(datetime.timezone.utc) - result[1] < datetime.timedelta(days=7):
                history = result[0]

        # ユーザー発話を追加
        history.append({"role": "user", "content": text})

        # GPT応答生成
        messages = [{"role": "system", "content": KAKERU_SYSTEM}] + history[-10:]
        reply_text = get_gpt_reply(messages)

        # 応答履歴を追加して保存
        history.append({"role": "assistant", "content": reply_text})
        cur.execute("""
            INSERT INTO user_data (user_id, history, last_updated)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                history = EXCLUDED.history,
                last_updated = EXCLUDED.last_updated;
        """, (user_id, Json(history), datetime.datetime.now(datetime.timezone.utc)))

        conn.commit()
    conn.close()

    safe_reply(event.reply_token, reply_text)

# === GPT呼び出し ===
def get_gpt_reply(messages):
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": messages,
                "temperature": 0.8,
            },
            timeout=20
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[OpenAI Error]: {e}")
        return "今ちょっと混線してるみたい。もう一度話してみて！"

# === モデレーション ===
def is_flagged(text):
    try:
        r = requests.post(
            "https://api.openai.com/v1/moderations",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": "omni-moderation-latest", "input": text},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()["results"][0]["flagged"]
    except:
        return False

# === LINE返信関数 ===
def safe_reply(reply_token, text):
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message_with_http_info(
                ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=text)])
            )
    except Exception as e:
        print(f"[LINE Reply Error]: {e}")

# === 占いAPI（RenderのCron用） ===
@app.route("/cron/daily-uranai", methods=["POST"])
def cron_daily():
    if request.headers.get("X-Cron-Token") != CRON_TOKEN:
        abort(401)

    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM user_data;")
        users = cur.fetchall()
    conn.close()

    if not users:
        return "OK"

    fortune = get_daily_fortune()
    push_message = f"🌅今日の恋愛運🌅\n{fortune}"

    for (user_id,) in users:
        try:
            requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"},
                json={"to": user_id, "messages": [{"type": "text", "text": push_message}]},
                timeout=5
            )
        except Exception as e:
            print(f"Push failed for {user_id}: {e}")

    return "OK"

def get_daily_fortune():
    prompt = "男性向けにポジティブな恋愛運を一言で占ってください。"
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0.85},
            timeout=15
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except:
        return "今日は自然体でいることが恋のカギ。焦らずいこう！"

# === Render起動設定 ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
