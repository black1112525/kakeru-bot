import os
import sys
import time
from contextlib import contextmanager
import psycopg2
from psycopg2 import OperationalError
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
from openai import OpenAI  # ✅ 新SDK対応

# --- Flask初期化 ---
app = Flask(__name__)

# --- 環境変数 ---
DATABASE_URL = os.getenv("DATABASE_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

# --- チェック ---
if not all([DATABASE_URL, LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, OPENAI_API_KEY]):
    print("⚠️ Render環境変数が不足しています。")
    sys.exit(1)

# --- 初期化 ---
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # ✅ proxiesバグ完全対応

# --- DB接続 ---
def connect_db(retry=3, wait=3):
    for i in range(retry):
        try:
            return psycopg2.connect(DATABASE_URL)
        except OperationalError as e:
            print(f"[DB接続失敗] {i+1}/{retry}回目: {e}")
            time.sleep(wait)
    print("⚠️ DB接続できませんでした。")
    return None

@contextmanager
def get_db():
    conn = connect_db()
    if not conn:
        yield None
        return
    try:
        yield conn
    finally:
        conn.close()

# --- DB初期化 ---
def init_db():
    with get_db() as conn:
        if not conn:
            return
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
init_db()

# --- 管理者通知 ---
def notify_admin(msg):
    if not ADMIN_ID:
        return
    try:
        line_bot_api.push_message(ADMIN_ID, TextSendMessage(text=f"[BOT通知]\n{msg}"))
    except Exception as e:
        print(f"[通知エラー] {e}")

# --- ChatGPT処理 ---
def chat_with_gpt(user_input, history_text="", retry=2):
    if history_text is None:
        history_text = ""
    for i in range(retry):
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": (
                        "あなたの名前はカケル。男性向け恋愛相談AIです。"
                        "落ち着いた優しい口調で、共感重視の受け答えをします。"
                        "専門的な診断・法律・医療の話題は避けます。"
                        "返答は800文字以内で、丁寧に優しく。"
                    )},
                    {"role": "user", "content": user_input}
                ],
                temperature=0.8,
                timeout=40
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[OpenAI通信失敗 {i+1}/{retry}] {e}")
            time.sleep(2)
    notify_admin("OpenAI通信に失敗しました。")
    return "ごめんね💦 今ちょっと混み合ってるみたい。もう少ししてから話しかけてみて！"

# --- 履歴保存 ---
def save_user_data(user_id, user_input, reply_text, history_text, talk_count):
    new_history = (history_text or "") + f"\n[ユーザー] {user_input}\n[カケル] {reply_text}"
    new_history = "\n".join(new_history.splitlines()[-20:])  # 最新20件のみ保持
    with get_db() as conn:
        if not conn:
            print("[DB未接続: 履歴保存スキップ]")
            return
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO user_data (user_id, history, talk_count, last_updated)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET history=%s, talk_count=user_data.talk_count+1, last_updated=NOW();
            """, (user_id, new_history, talk_count + 1, new_history))
            conn.commit()
        except Exception as e:
            print(f"[DB保存エラー] {e}")
            notify_admin(f"DB保存エラー: {e}")

# --- LINE返信 ---
def safe_reply(reply_token, message, retry=2):
    for i in range(retry):
        try:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=message))
            return
        except Exception as e:
            print(f"[LINE送信エラー {i+1}/{retry}] {e}")
            time.sleep(1)
    notify_admin("LINE送信失敗")

# --- Webhook受信 ---
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"[Webhookエラー] {e}")
        notify_admin(f"Webhookエラー: {e}")
        abort(400)
    return "OK"

# --- 友だち追加時 ---
@handler.add(FollowEvent)
def handle_follow(event):
    welcome = (
        "🌙 こんばんは！カケルです。\n\n"
        "男性のための恋愛相談AIとして、あなたの話をじっくり聞きます。\n"
        "気軽に話しかけてください😊"
    )
    safe_reply(event.reply_token, welcome)

# --- 通常メッセージ ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text.strip()
    with get_db() as conn:
        if not conn:
            safe_reply(event.reply_token, "今サーバーがちょっと休んでるみたい💤 また話しかけてね。")
            return
        cur = conn.cursor()
        cur.execute("SELECT history, talk_count FROM user_data WHERE user_id=%s;", (user_id,))
        row = cur.fetchone()
        history_text, talk_count = (row if row else ("", 0))
    reply_text = chat_with_gpt(user_input, history_text)
    save_user_data(user_id, user_input, reply_text, history_text, talk_count)
    safe_reply(event.reply_token, reply_text)

# --- ヘルスチェック ---
@app.route("/health")
def health():
    return "OK", 200

# --- 起動 ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
