import os
import sys
import psycopg2
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import MessagingApi, TextMessage
from linebot.v3.models import ReplyMessageRequest
from linebot.v3.http_client import ApiClient
from openai import OpenAI

# === Flask設定 ===
app = Flask(__name__)

# === 環境変数 ===
DATABASE_URL = os.getenv("DATABASE_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not all([DATABASE_URL, LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, OPENAI_API_KEY]):
    print("⚠️ 環境変数が不足しています。Renderの設定を確認してください。")
    sys.exit(1)

# === 初期設定 ===
client = OpenAI(api_key=OPENAI_API_KEY)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = {"access_token": LINE_CHANNEL_ACCESS_TOKEN}

# === DB初期化 ===
def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_data (
            user_id TEXT PRIMARY KEY,
            talk_count INTEGER DEFAULT 0,
            history TEXT,
            last_updated TIMESTAMP DEFAULT NOW()
        );
    """)
    conn.commit()
    conn.close()

init_db()

# === GPT応答関数 ===
def chat_with_gpt(user_input, history_text=""):
    if history_text is None:
        history_text = ""

    try:
        messages = [
            {"role": "system", "content": (
                "あなたの名前はカケル。男性向け恋愛カウンセラーAI。"
                "優しく落ち着いた口調で、相手の悩みを受け止める。"
                "初対面では丁寧に、慣れてきたら少しフランクに接して良い。"
                "相談者を否定せず共感を重視。アドバイスは前向きに。"
                "専門的な診断や法律的助言は行わず、一般的なサポートを提供する。"
                "一度の返答は800文字以内。"
            )}
        ]

        if history_text:
            messages.append({"role": "assistant", "content": f"前回までの会話履歴: {history_text}"})

        messages.append({"role": "user", "content": user_input})

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.8,
            timeout=40
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"[OpenAIエラー] {e}")
        return "通信が少し不安定みたいです。もう一度話しかけてください。"

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

# === Webhook設定 ===
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"[Webhookエラー] {e}")
        abort(400)
    return "OK"

# === メッセージ処理 ===
@handler.add("message", message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text.strip()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT history, talk_count FROM user_data WHERE user_id=%s;", (user_id,))
    row = cur.fetchone()

    if row:
        history_text, talk_count = row
    else:
        history_text, talk_count = ("", 0)

    reply_text = chat_with_gpt(user_input, history_text)

    new_history = (history_text or "") + f"\n[ユーザー] {user_input}\n[カケル] {reply_text}"
    cur.execute("""
        INSERT INTO user_data (user_id, history, talk_count, last_updated)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET history=%s, talk_count=user_data.talk_count+1, last_updated=NOW();
    """, (user_id, new_history, talk_count + 1, new_history))

    conn.commit()
    conn.close()

    safe_reply(event.reply_token, reply_text)

# === DBリセット（必要な時のみ使用） ===
@app.route("/reset-db")
def reset_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS user_data;")
    cur.execute("""
        CREATE TABLE user_data (
            user_id TEXT PRIMARY KEY,
            talk_count INTEGER DEFAULT 0,
            history TEXT,
            last_updated TIMESTAMP DEFAULT NOW()
        );
    """)
    conn.commit()
    conn.close()
    return "✅ データベースをリセットしました！"

# === アプリ起動 ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
