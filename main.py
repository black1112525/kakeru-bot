import os
import sys
import psycopg2
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
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

# === 各API初期化 ===
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

# === データベース初期化 ===
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
                "優しく落ち着いた口調で相手の悩みを受け止め、共感を重視する。"
                "初対面では丁寧に、慣れたらフランクでもOK。"
                "専門的な診断・法的助言は避け、一般的なアドバイスを中心に。"
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
        return "少し通信が不安定みたいです。また話しかけてくださいね。"

# === LINE返信関数 ===
def safe_reply(reply_token, message):
    try:
        line_bot_api.reply_message(reply_token, TextSendMessage(text=message))
    except Exception as e:
        print(f"[LINE送信エラー] {e}")

# === Webhookエンドポイント ===
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"[Webhookエラー] {e}")
        abort(400)
    return "OK"

# === メッセージ受信処理 ===
@handler.add(MessageEvent, message=TextMessage)
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

# === DBリセット ===
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
