from flask import Flask, request, abort
import os, requests, random
from datetime import datetime, timedelta
import pytz
from supabase import create_client, Client

app = Flask(__name__)

# ===== 環境変数 =====
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")
CRON_KEY = os.getenv("CRON_KEY", "yukito")

# ===== Supabase 接続 =====
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===== LINEメッセージ送信 =====
def send_line_message(user_id, text):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {"to": user_id, "messages": [{"type": "text", "text": text}]}
    requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=data)

# ===== Supabaseログ保存 =====
def log_message_to_supabase(user_id, message, log_type="auto"):
    try:
        data = {
            "user_id": user_id,
            "message": message,
            "type": log_type,
            "created_at": datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()
        }
        supabase.table("logs").insert(data).execute()
        print(f"✅ Supabaseログ保存成功: {message}")
    except Exception as e:
        print(f"❌ Supabaseログ保存エラー: {e}")

# ===== 月曜 =====
@app.route("/cron/monday")
def monday():
    if request.args.get("key") != CRON_KEY: abort(403)
    msg = "🌞月曜メッセージ：新しい週の始まり！前向きにスタートしよう！"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "monday")
    return "✅ Monday sent"

# ===== 水曜 =====
@app.route("/cron/wednesday")
def wednesday():
    if request.args.get("key") != CRON_KEY: abort(403)
    msg = "🌿水曜メッセージ：週の折り返し。焦らずリズムを整えて。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "wednesday")
    return "✅ Wednesday sent"

# ===== 金曜 =====
@app.route("/cron/friday")
def friday():
    if request.args.get("key") != CRON_KEY: abort(403)
    msg = "🎉金曜メッセージ：1週間お疲れさま！少し自分を褒めよう！"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "friday")
    return "✅ Friday sent"

# ===== 日曜 =====
@app.route("/cron/sunday")
def sunday():
    if request.args.get("key") != CRON_KEY: abort(403)
    msg = "🌙日曜メッセージ：今週もお疲れさま。感謝してリセットしよう。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "sunday")
    return "✅ Sunday sent"

# ===== モーニングおみくじ =====
@app.route("/cron/omikuji")
def omikuji():
    if request.args.get("key") != CRON_KEY: abort(403)
    fortunes = [
        "大吉🌸 最高の一日が待ってる！",
        "中吉🌼 いい流れが来てるよ！",
        "小吉🍀 穏やかに過ごせそう。",
        "吉🌿 チャンスは自分から動くと掴める！",
        "凶💧 無理せず休む日。リセットしよう。"
    ]
    msg = f"☀️おはよう！今日の運勢は…\n{random.choice(fortunes)}"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "omikuji")
    return "✅ Omikuji sent"

# ===== 満月・新月オート配信 =====
@app.route("/cron/moon_auto")
def moon_auto():
    if request.args.get("key") != CRON_KEY: abort(403)
    now = datetime.now(pytz.timezone("Asia/Tokyo"))
    day = now.day
    if day in [1, 15, 16]:
        msg = "🌕スピリチュアルメッセージ：月のエネルギーを感じて、自分を整えよう✨"
        send_line_message(ADMIN_ID, msg)
        log_message_to_supabase(ADMIN_ID, msg, "moon_auto")
        return "✅ Moon message sent"
    return "🌑 Not moon day"

# ===== 週間レポート =====
@app.route("/cron/weekly_report")
def weekly_report():
    if request.args.get("key") != CRON_KEY: abort(403)
    try:
        now = datetime.now(pytz.timezone("Asia/Tokyo"))
        start = now - timedelta(days=7)
        res = supabase.table("logs").select("*").gte("created_at", start.isoformat()).execute()
        logs = res.data

        if not logs:
            report = "📊 今週のレポート\n配信記録はありませんでした。"
        else:
            report = "📊 【カケル週間レポート】\n\n"
            total = len(logs)
            report += f"今週の配信数：{total}件\n\n"
            type_count = {}
            for log in logs:
                t = log["type"]
                type_count[t] = type_count.get(t, 0) + 1
            for t, c in type_count.items():
                report += f"・{t}：{c}回\n"
            report += "\n🌙 次週もよろしくね！"

        send_line_message(ADMIN_ID, report)
        log_message_to_supabase(ADMIN_ID, "Weekly report sent ✅", "report")
        return "✅ Weekly report sent"
    except Exception as e:
        print("❌ Weekly report error:", e)
        return str(e)

# ===== 動作確認 =====
@app.route("/")
def home():
    return "✅ Kakeru Bot running successfully!"

# ===== メイン実行 =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
