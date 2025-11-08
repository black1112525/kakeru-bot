import os
import json
import time
import random
import threading
import requests
import pytz
import ephem
from datetime import datetime, timedelta
from flask import Flask, request, abort
from supabase import create_client, Client
from openai import OpenAI

# === Flask起動 ===
app = Flask(__name__)
TZ = pytz.timezone("Asia/Tokyo")

# === 環境変数 ===
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")
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

# === LINE送信関数 ===
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
        res = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers=headers,
            json=data
        )
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

# === 認証チェック ===
def check_key():
    if request.args.get("key") != CRON_KEY:
        abort(403)

# === 過去会話取得 ===
def get_recent_conversation(user_id, limit=10):
    try:
        res = (
            supabase.table("logs")
            .select("message, type")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
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
    system_prompt = (
        "あなたは『カケル』という誠実で優しい恋愛相談員です。\n"
        "相手の気持ちを受け止め、安心できる言葉を2〜4文で返してください。\n"
        "優しく丁寧なトーンで話し、急かさず共感を大切にしてください。"
    )

    history = get_recent_conversation(user_id)
    messages = [{"role": "system", "content": system_prompt}] + history
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.8,
            max_tokens=160,
        )
        reply = response.choices[0].message.content.strip()
        return reply
    except Exception as e:
        print(f"❌ OpenAI返答エラー: {e}")
        return "ごめんね、少し考え込んでしまった。もう一度話してもらえる？"

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

# === 月相メッセージ ===
@app.route("/cron/moon_auto")
def moon_auto():
    check_key()
    now = datetime.now(TZ)
    moon = ephem.Moon(now)
    age = moon.phase
    if age < 1.5:
        msg = "🌑新月メッセージ：静けさの中で新しい願いを描こう。"
    elif age < 15.5:
        msg = "🌕満月メッセージ：感謝と共に手放そう。"
    else:
        msg = "🌖月の光メッセージ：心を整えて、深呼吸を忘れずに。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "moon_auto")
    return f"✅ Moon sent ({age:.1f})"

# === 週報生成 ===
@app.route("/cron/weekly_report")
def weekly_report():
    check_key()
    try:
        now = datetime.now(TZ)
        start = now - timedelta(days=7)
        res = supabase.table("logs").select("*").gte("created_at", start.isoformat()).execute()
        logs = res.data

        if not logs:
            report = "📊今週のログはありませんでした。"
        else:
            total = len(logs)
            types = {}
            for l in logs:
                t = l["type"]
                types[t] = types.get(t, 0) + 1
            report = f"📊【カケル週報】\\n件数: {total}\\n" + "\\n".join([f\"{k}: {v}\" for k,v in types.items()])

            ai = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "あなたは恋愛相談AI『カケル』の運用アシスタントです。"},
                    {"role": "user", "content": f"以下ログをもとに簡潔な運用分析をしてください:\\n{json.dumps(logs)[:4000]}"}
                ]
            )
            summary = ai.choices[0].message.content.strip()
            report += f"\\n🧠AI分析:\\n{summary}"

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

# === メイン ===
if __name__ == "__main__":
    keep_alive()
    app.run(host="0.0.0.0", port=10000)
