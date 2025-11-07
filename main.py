import os
import json
import random
import requests
import pytz
from datetime import datetime, timedelta
from flask import Flask, request, abort
from supabase import create_client, Client
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI

# ===== 環境変数 =====
ADMIN_ID = os.getenv("ADMIN_ID")
CRON_KEY = os.getenv("CRON_KEY")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ===== 初期化 =====
app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

# ===== 共通関数 =====
def log_message_to_supabase(user_id, message, log_type="user"):
    try:
        data = {
            "user_id": user_id,
            "message": message,
            "type": log_type,
            "created_at": datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()
        }
        supabase.table("logs").insert(data).execute()
        print(f"✅ Supabaseログ保存: {log_type}")
    except Exception as e:
        print(f"❌ Supabase保存エラー: {e}")

def send_line_message(user_id, text):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {"to": user_id, "messages": [{"type": "text", "text": text}]}
    requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=data)

# ===== 共感AI返信 =====
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text.strip()

    log_message_to_supabase(user_id, user_msg, "user")

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "あなたは男性向け恋愛相談AI『カケル』です。"
                        "相手の感情を大切にし、まず共感を伝えてから、"
                        "優しく実用的なアドバイスをします。"
                        "最後は一言で前向きな励ましを添えます。"
                        "口調は『俺』が一人称で、親しみと誠実さを大切に。"
                        "例：『それはつらかったな。でも大丈夫、ちゃんと前に進めるよ。』"
                    )
                },
                {"role": "user", "content": user_msg}
            ]
        )
        ai_reply = response.choices[0].message.content.strip()

    except Exception as e:
        print("❌ OpenAIエラー:", e)
        ai_reply = "ごめん、ちょっと混み合ってるみたい。もう一度話しかけてくれる？"

    # LINEへ返信
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=ai_reply)
    )

    log_message_to_supabase(user_id, ai_reply, "bot")


# ===== 定期配信（既存のcron機能） =====
@app.route("/cron/monday")
def monday():
    if request.args.get("key") != CRON_KEY:
        abort(403)
    msg = "🌞 月曜メッセージ：新しい週の始まり！前向きにスタートしよう！"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "monday")
    return "✅ Monday sent"

@app.route("/cron/omikuji")
def omikuji():
    if request.args.get("key") != CRON_KEY:
        abort(403)
    fortunes = [
        "大吉 🌟 最高の一日が待ってる！",
        "中吉 😊 いい流れが来そう！",
        "小吉 🍀 穏やかに過ごせそう。",
        "吉 ✨ チャンスは自分から掴もう！",
        "凶 💧 無理せず休もう。"
    ]
    msg = f"🎯 おはよう！今日の運勢は…\n{random.choice(fortunes)}"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "omikuji")
    return "✅ Omikuji sent"


# ===== Webhook受信 =====
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# ===== 動作確認 =====
@app.route("/")
def home():
    return "🚀 Kakeru Bot running with Empathic AI reply!"


# ===== メイン =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
