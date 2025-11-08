import os
import json
import requests
import threading
import time
from datetime import datetime, timedelta
import pytz
import ephem
import random
from flask import Flask, request, abort
from supabase import create_client, Client
from openai import OpenAI

# Flaskアプリ起動
app = Flask(__name__)

# === 環境変数 ===
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID", "Uxxxxxxxx")  # 管理者LINE ID（必要に応じて変更）
CRON_KEY = os.getenv("CRON_KEY")

# === Supabase接続 ===
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized successfully")
except Exception as e:
    print(f"❌ Supabase connection error: {e}")
    supabase = None

# === OpenAIクライアント ===
client = OpenAI(api_key=OPENAI_API_KEY)

# === LINE送信 ===
def send_line_message(user_id, text):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {
        "to": user_id,
        "messages": [{"type": "text", "text": text[:490]}]
    }
    try:
        res = requests.post("https://api.line.me/v2/bot/message/push",
                            headers=headers, json=data)
        print(f"📤 LINE送信成功: {res.status_code}")
    except Exception as e:
        print(f"❌ LINE送信エラー: {e}")

# === Supabaseログ保存 ===
def log_message_to_supabase(user_id, message, log_type="auto"):
    if not supabase:
        print("⚠️ Supabase未接続。ログは保存されません。")
        return
    try:
        data = {
            "user_id": user_id,
            "message": message,
            "type": log_type,
            "created_at": datetime.now(pytz.timezone("Asia/Tokyo")).isoformat(),
        }
        supabase.table("logs").insert(data).execute()
        print(f"🗂 Supabaseログ保存成功: {log_type}")
    except Exception as e:
        print(f"❌ Supabaseログ保存エラー: {e}")

# === 認証チェック ===
def check_key():
    if request.args.get("key") != CRON_KEY:
        abort(403)

# === AI返信生成 ===
def generate_ai_reply(user_id, user_message):
    system_prompt = (
        "あなたは『カケル』という誠実で優しい恋愛相談員です。\n"
        "相手の気持ちを理解し、共感と前向きなアドバイスを返してください。\n"
        "2〜3文で優しく自然な日本語で答えてください。\n"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.8,
        )
        reply = response.choices[0].message.content.strip()
        return reply
    except Exception as e:
        print(f"❌ OpenAI返答エラー: {e}")
        return "ごめんなさい、少し考え込んでしまいました。もう一度話してもらえますか？"

# === Webhook受信 ===
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()
    events = body.get("events", [])

    for event in events:
        if event["type"] == "message" and event["message"]["type"] == "text":
            user_id = event["source"]["userId"]
            user_message = event["message"]["text"]
            print(f"💬 {user_id}: {user_message}")

            reply = generate_ai_reply(user_id, user_message)
            send_line_message(user_id, reply)
            log_message_to_supabase(user_id, user_message, "user")
            log_message_to_supabase(user_id, reply, "ai")

    return "OK"

# === 定期配信 ===
@app.route("/cron/monday")
def monday():
    check_key()
    msg = "🌅月曜メッセージ：新しい週の始まり、焦らず少しずつ進もう。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "monday")
    return "✅ Monday sent"

@app.route("/cron/wednesday")
def wednesday():
    check_key()
    msg = "🌤水曜メッセージ：週の折り返し、リズムを整えてね。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "wednesday")
    return "✅ Wednesday sent"

@app.route("/cron/friday")
def friday():
    check_key()
    msg = "🌙金曜メッセージ：1週間お疲れさま。今夜はゆっくり休もう。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "friday")
    return "✅ Friday sent"

@app.route("/cron/sunday")
def sunday():
    check_key()
    msg = "☀️日曜メッセージ：今週もよく頑張りましたね。感謝してリセットしよう。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "sunday")
    return "✅ Sunday sent"

@app.route("/cron/omikuji")
def omikuji():
    check_key()
    fortunes = [
        "大吉：最高の一日になりそうです！",
        "中吉：いい流れが来ていますよ。",
        "小吉：穏やかな日になりそう。",
        "吉：焦らず進めばうまくいきます。",
        "凶：今日は自分を労わる日です。"
    ]
    msg = f"🎲おみくじ：{random.choice(fortunes)}"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "omikuji")
    return "✅ Omikuji sent"

# === 週次レポート ===
@app.route("/cron/weekly_report")
def weekly_report():
    check_key()
    try:
        now = datetime.now(pytz.timezone("Asia/Tokyo"))
        start = now - timedelta(days=7)
        res = supabase.table("logs").select("*").gte("created_at", start.isoformat()).execute()
        logs = res.data

        report = "📊【カケル週報】\n\n"
        report += f"記録件数：{len(logs)}件\n"
        ai_messages = [l for l in logs if l["type"] == "ai"]
        report += f"AI返信数：{len(ai_messages)}件\n"

        ai_summary = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "あなたは恋愛相談AI『カケル』の週報アシスタントです。"},
                {"role": "user", "content": f"以下は今週の会話ログです:\n{logs}"}
            ]
        )
        summary = ai_summary.choices[0].message.content.strip()
        report += "\n🧠【AI分析】\n" + summary

        send_line_message(ADMIN_ID, report[:490])
        log_message_to_supabase(ADMIN_ID, report, "weekly_report")
        return "✅ Weekly report sent"

    except Exception as e:
        print(f"❌ Weekly report error: {e}")
        return str(e)

# === Renderスリープ防止 ===
def keep_alive():
    def ping():
        while True:
            try:
                requests.get("https://kakeru-bot-1.onrender.com/")
                print("🔁 Ping sent to keep Render awake")
            except Exception as e:
                print(f"⚠️ Keep-alive ping error: {e}")
            time.sleep(600)

    thread = threading.Thread(target=ping)
    thread.daemon = True
    thread.start()

# === 動作確認 ===
@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def home():
    return "🌸 Kakeru Bot running gently with memory!"

# === メイン実行 ===
if __name__ == "__main__":
    keep_alive()
    app.run(host="0.0.0.0", port=10000)
