import os
import sys
import json
import psycopg2
from datetime import datetime, timezone
from flask import Flask, request, abort
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
import openai

# === Flask設定 ===
app = Flask(__name__)

# === 環境変数 ===
DATABASE_URL = os.getenv("DATABASE_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not all([DATABASE_URL, LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, OPENAI_API_KEY]):
    print("❌ 環境変数が不足しています。Renderの設定を確認してください。")
    sys.exit(1)

openai.api_key = OPENAI_API_KEY
handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# === データベース初期化 ===
def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_data (
            user_id TEXT PRIMARY KEY,
            talk_count INTEGER DEFAULT 0,
            history TEXT,
            last_updated TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

init_db()

# === GPT応答関数（優しく丁寧な人格設定）===
def chat_with_gpt(user_input, history_text=""):
    try:
        messages = [
            {"role": "system", "content": (
                "あなたの名前はカケル。男性向け恋愛カウンセラーAI。"
                "基本は丁寧で落ち着いた口調。初対面では丁寧に、"
                "慣れてきたら少しくだけた言葉や軽い冗談も交えて良い。"
                "相談者を否定せず共感を重視。アドバイスは前向きで優しく。"
                "医療・法律などの専門相談は勧めず、一般的な助言のみ。"
                "一度の返信は800文字以内。"
            )}
        ]
        if history_text:
            messages.append({"role": "assistant", "content": f"前回までの会話履歴: {history_text}"})
        messages.append({"role": "user", "content": user_input})

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.8,
            timeout=40
        )

        reply = response["choices"][0]["message"]["content"].strip()
        return reply
    except Exception as e:
        print(f"[OpenAIエラー] {e}")
        return "すみません💦　少し通信が不安定みたいです。もう一度話してもらえますか？"

# === LINE返信関数 ===
def safe_reply(reply_token, message):
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=message)]
                )
            )
    except Exception as e:
        print(f"[LINE送信エラー] {e}")

# === LINE Webhook ===
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# === メッセージ受信処理 ===
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # 履歴取得
    cur.execute("SELECT history, talk_count FROM user_data WHERE user_id = %s;", (user_id,))
    row = cur.fetchone()
    history_text = row[0] if row else ""
    talk_count = row[1] if row else 0

    # GPT応答
    reply = chat_with_gpt(text, history_text)

    # 履歴更新
    new_history = (history_text + "\n[ユーザー] " + text + "\n[カケル] " + reply).strip()
    talk_count += 1

    cur.execute("""
        INSERT INTO user_data (user_id, talk_count, history, last_updated)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET talk_count = %s, history = %s, last_updated = %s;
    """, (
        user_id, talk_count, new_history, datetime.now(timezone.utc),
        talk_count, new_history, datetime.now(timezone.utc)
    ))

    conn.commit()
    cur.close()
    conn.close()

    safe_reply(event.reply_token, reply)

# === Render起動設定 ===
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
