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
TZ = pytz.timezone("Asia/Tokyo")

# === 環境変数 ===
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID", "Uxxxxxxxx")
CRON_KEY = os.getenv("CRON_KEY")

# === Supabase接続 ===
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized successfully")
except Exception as e:
    print(f"❌ Supabase connection error: {e}")
    supabase = None

# === OpenAI ===
client = OpenAI(api_key=OPENAI_API_KEY)

# === LINE送信 ===
def send_line_message(user_id, text):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {"to": user_id, "messages": [{"type": "text", "text": text[:490]}]}
    try:
        res = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=data)
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
            "created_at": datetime.now(TZ).isoformat(),
        }
        supabase.table("logs").insert(data).execute()
        print(f"🗂 Supabaseログ保存成功: {log_type}")
    except Exception as e:
        print(f"❌ Supabaseログ保存エラー: {e}")

# === ユーザープロフィール保存 ===
def save_user_profile(user_id, gender=None, status=None, feeling=None, plan="free"):
    if not supabase:
        print("⚠️ Supabase未接続。ユーザープロフィールは保存されません。")
        return
    try:
        data = {
            "user_id": user_id,
            "gender": gender,
            "status": status,
            "feeling": feeling,
            "plan": plan,
            "updated_at": datetime.now(TZ).isoformat(),
        }
        supabase.table("users").upsert(data, on_conflict="user_id").execute()
        print(f"🧍ユーザープロフィール保存成功: {user_id}")
    except Exception as e:
        print(f"❌ユーザープロフィール保存エラー: {e}")

# === 会話履歴取得 ===
def get_recent_conversation(user_id, limit=10):
    if not supabase:
        return []
    try:
        res = supabase.table("logs").select("message, type").eq("user_id", user_id).order("created_at", desc=True).limit(limit).execute()
        logs = res.data[::-1]
        conversation = []
        for log in logs:
            if log["type"] == "user":
                conversation.append({"role": "user", "content": log["message"]})
            elif log["type"] == "ai":
                conversation.append({"role": "assistant", "content": log["message"]})
        return conversation
    except Exception as e:
        print(f"⚠️ 会話履歴取得エラー: {e}")
        return []

# === AI返信生成 ===
def generate_ai_reply(user_id, user_message):
    # ユーザー属性を取得
    user_info = supabase.table("users").select("*").eq("user_id", user_id).execute().data
    gender = user_info[0]["gender"] if user_info else "未設定"
    status = user_info[0]["status"] if user_info else "不明"

    system_prompt = (
        f"あなたは『カケル』という誠実で優しい恋愛相談員です。\n"
        f"性別: {gender}\n"
        f"状況: {status}\n"
        "相手の気持ちを受け止め、共感を伝え、安心できる言葉を返してください。\n"
        "丁寧で優しい言葉遣いで2〜4文にまとめてください。"
    )

    history = get_recent_conversation(user_id, limit=10)
    messages = [{"role": "system", "content": system_prompt}] + history
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.8,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ OpenAI返答エラー: {e}")
        return "ごめんなさい、少し考え込んでしまいました。もう一度話してもらえますか？"

# === Webhook（質問フロー付き） ===
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()
    events = body.get("events", [])

    for event in events:
        if event["type"] == "message" and event["message"]["type"] == "text":
            user_id = event["source"]["userId"]
            user_message = event["message"]["text"]

            # 新規ユーザー確認
            res = supabase.table("users").select("*").eq("user_id", user_id).execute()
            is_new = len(res.data) == 0

            if is_new:
                send_line_message(user_id,
                    "はじめまして、カケルです。\nあなたの恋の状況を少し教えてください。\nまず、性別を教えてください（男性／女性／その他）")
                save_user_profile(user_id)
                return "OK"

            user_data = res.data[0]

            if not user_data.get("gender"):
                supabase.table("users").update({"gender": user_message}).eq("user_id", user_id).execute()
                send_line_message(user_id, "ありがとう😊\n次に、今の恋愛の状況を教えてください（片思い・交際中・失恋・その他）")
                return "OK"

            elif not user_data.get("status"):
                supabase.table("users").update({"status": user_message}).eq("user_id", user_id).execute()
                send_line_message(user_id, "なるほど…！\n最後に、今の気持ちをひとことで教えてください（例：寂しい・モヤモヤ・楽しいなど）")
                return "OK"

            elif not user_data.get("feeling"):
                supabase.table("users").update({"feeling": user_message}).eq("user_id", user_id).execute()
                send_line_message(user_id, "ありがとう。あなたの気持ち、大切に受け取りました。\nこれから一緒に考えていこう。")
                return "OK"

            # 通常AI応答
            reply = generate_ai_reply(user_id, user_message)
            send_line_message(user_id, reply)
            log_message_to_supabase(user_id, user_message, "user")
            log_message_to_supabase(user_id, reply, "ai")

    return "OK"

# === 定期配信など（君の現行コードそのまま） ===
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

# === 週次レポートなど（現行維持） ===
@app.route("/cron/weekly_report")
def weekly_report():
    check_key()
    try:
        now = datetime.now(TZ)
        start = now - timedelta(days=7)
        res = supabase.table("logs").select("*").gte("created_at", start.isoformat()).execute()
        logs = res.data
        report = f"📊【カケル週報】\n記録件数：{len(logs)}件\n"
        ai_messages = [l for l in logs if l["type"] == "ai"]
        report += f"AI返信数：{len(ai_messages)}件\n"
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

@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def home():
    return "🌸 Kakeru Bot running gently with memory!"

if __name__ == "__main__":
    keep_alive()
    app.run(host="0.0.0.0", port=10000)
