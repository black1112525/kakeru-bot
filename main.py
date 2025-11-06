import os
import random
import pytz
import requests
import threading
import time
import ephem
from datetime import datetime, timedelta
from flask import Flask, request, abort
from supabase import create_client, Client

# ===== Flask初期化 =====
app = Flask(__name__)

# ===== 環境変数 =====
ADMIN_ID = os.getenv("ADMIN_ID")
CRON_KEY = os.getenv("CRON_KEY", "yukito")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ===== Supabase接続 =====
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized successfully")
except Exception as e:
    print(f"❌ Supabase connection error: {e}")
    supabase = None

# ===== LINE送信関数 =====
def send_line_message(user_id, text):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    data = {"to": user_id, "messages": [{"type": "text", "text": text}]}
    try:
        res = requests.post("https://api.line.me/v2/bot/message/push",
                            headers=headers, json=data)
        print(f"✅ LINE送信成功: {res.status_code}")
    except Exception as e:
        print(f"❌ LINE送信エラー: {e}")

# ===== Supabaseログ保存 =====
def log_message_to_supabase(user_id, message, log_type="auto"):
    if not supabase:
        print("⚠ Supabase未接続。ログは保存されません。")
        return
    try:
        data = {
            "user_id": user_id,
            "message": message,
            "type": log_type,
            "created_at": datetime.now(pytz.timezone("Asia/Tokyo")).isoformat(),
        }
        supabase.table("logs").insert(data).execute()
        print(f"📝 Supabaseログ保存成功: {log_type}")
    except Exception as e:
        print(f"❌ Supabaseログ保存エラー: {e}")

# ===== 認証チェック =====
def check_key():
    if request.args.get("key") != CRON_KEY:
        abort(403)

# ===== 定期メッセージ =====
@app.route("/cron/monday")
def monday():
    check_key()
    msg = "🌅月曜メッセージ：新しい週の始まり！前向きにスタートしよう！"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "monday")
    return "✅ Monday sent"

@app.route("/cron/wednesday")
def wednesday():
    check_key()
    msg = "🌿水曜メッセージ：週の折り返し。焦らずリズムを整えて。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "wednesday")
    return "✅ Wednesday sent"

@app.route("/cron/friday")
def friday():
    check_key()
    msg = "🌙金曜メッセージ：1週間お疲れさま！少し自分を褒めよう！"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "friday")
    return "✅ Friday sent"

@app.route("/cron/sunday")
def sunday():
    check_key()
    msg = "☕日曜メッセージ：今週もお疲れさま。感謝してリセットしよう。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "sunday")
    return "✅ Sunday sent"

# ===== モーニングおみくじ =====
@app.route("/cron/omikuji")
def omikuji():
    check_key()
    fortunes = [
        "🌞大吉：最高の一日が待ってる！",
        "🍀中吉：いい流れが来るよ！",
        "🌸小吉：穏やかに過ごせそう。",
        "🌾吉：チャンスは自分から掴もう！",
        "🌧凶：無理せず休む日。リセットしよう。",
    ]
    msg = f"🌅おはよう！今日の運勢は…\n{random.choice(fortunes)}"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "omikuji")
    return "✅ Omikuji sent"

# ===== 満月・新月自動判定 =====
@app.route("/cron/moon_auto")
def moon_auto():
    check_key()
    now = datetime.now(pytz.timezone("Asia/Tokyo"))
    
    moon = ephem.Moon()
    moon.compute(now)
    moon_age = moon.phase  # 月齢（0＝新月, 14〜15＝満月）

    msg = None
    if moon_age < 1.5:
        msg = "🌑 新月メッセージ：静けさの中で新しい願いを描こう。"
    elif 14 <= moon_age <= 15.5:
        msg = "🌕 満月メッセージ：感謝と共に手放す日。月の光を感じて過ごそう。"

    if msg:
        send_line_message(ADMIN_ID, msg)
        log_message_to_supabase(ADMIN_ID, msg, "moon_auto")
        return f"✅ {msg[:2]} Message sent"
    else:
        print(f"🌙 月齢: {moon_age:.1f} → 配信なし")
        return "ℹ Not moon day"

# ===== 週間レポート =====
@app.route("/cron/weekly_report")
def weekly_report():
    check_key()
    try:
        now = datetime.now(pytz.timezone("Asia/Tokyo"))
        start = now - timedelta(days=7)
        res = supabase.table("logs").select("*").gte("created_at", start.isoformat()).execute()
        logs = res.data

        if not logs:
            report = "📊今週のレポート\n配信記録はありませんでした。"
        else:
            report = "📊【カケル週間レポート】\n\n"
            total = len(logs)
            report += f"配信総数：{total}件\n\n"
            type_count = {}
            for log in logs:
                t = log["type"]
                type_count[t] = type_count.get(t, 0) + 1
            for t, c in type_count.items():
                report += f"{t}：{c}回\n"
            report += "\n🌙次週もよろしくね！"

        send_line_message(ADMIN_ID, report)
        log_message_to_supabase(ADMIN_ID, report, "weekly_report")
        return "✅ Weekly report sent"
    except Exception as e:
        print(f"❌ Weekly report error: {e}")
        return str(e)

# ===== スリープ防止（Render Keep Alive）=====
def keep_alive():
    def ping():
        while True:
            try:
                requests.get("https://kakeru-bot-1.onrender.com/")
                print("💤 Ping sent to keep Render awake")
            except Exception as e:
                print(f"⚠️ Keep-alive ping error: {e}")
            time.sleep(600)  # 10分ごとにPing送信
    thread = threading.Thread(target=ping)
    thread.daemon = True
    thread.start()

# ===== 動作確認 =====
@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def home():
    return "✅ Kakeru Bot running successfully!"

# ===== メイン実行 =====
if __name__ == "__main__":
    keep_alive()
    app.run(host="0.0.0.0", port=10000)
