from flask import Flask, request, abort
import os
import requests
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client

# Flask アプリ初期化
app = Flask(__name__)

# === 環境変数 ===
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
CRON_KEY = os.environ.get("CRON_KEY")
ADMIN_ID = os.environ.get("ADMIN_ID")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === 共通関数 ===

def send_line_message(to, text):
    """LINEメッセージ送信"""
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    body = {
        "to": to,
        "messages": [{"type": "text", "text": text}]
    }
    requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=body)

def log_message_to_supabase(user_id, message, msg_type):
    """Supabaseにログを保存"""
    supabase.table("logs").insert({
        "user_id": user_id,
        "message": message,
        "type": msg_type
    }).execute()

# === 各曜日の配信 ===

@app.route("/cron/monday")
def monday():
    if request.args.get("key") != CRON_KEY: abort(403)
    msg = "🌞月曜メッセージ：新しい週のスタート！ポジティブに始めよう！"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "monday")
    return "✅ Monday message sent"

@app.route("/cron/wednesday")
def wednesday():
    if request.args.get("key") != CRON_KEY: abort(403)
    msg = "🌿水曜メッセージ：週の折り返し！自分を褒めよう！"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "wednesday")
    return "✅ Wednesday message sent"

@app.route("/cron/friday")
def friday():
    if request.args.get("key") != CRON_KEY: abort(403)
    msg = "🎉金曜メッセージ：お疲れ様！週末を楽しんで！"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "friday")
    return "✅ Friday message sent"

@app.route("/cron/sunday")
def sunday():
    if request.args.get("key") != CRON_KEY: abort(403)
    msg = "🌙日曜メッセージ：今週もお疲れ様。感謝してリセットしよう。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "sunday")
    return "✅ Sunday message sent"

# === モーニングおみくじ ===
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
    import random
    msg = f"☀️おはよう！今日の運勢は…\n{random.choice(fortunes)}"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "omikuji")
    return "✅ Omikuji sent"

# === 満月・新月オート配信 ===
@app.route("/cron/moon_auto")
def moon_auto():
    if request.args.get("key") != CRON_KEY: abort(403)
    now = datetime.now(timezone(timedelta(hours=9)))
    day = now.day
    if day in [1, 15, 16]:  # 新月・満月前後
        msg = "🌕スピリチュアルメッセージ：満月・新月のエネルギーを感じて、自分を見つめ直そう✨"
        send_line_message(ADMIN_ID, msg)
        log_message_to_supabase(ADMIN_ID, msg, "moon_auto")
        return "✅ Moon message sent"
    return "🌑 Not moon day"

# === 週次レポート（Supabase集計） ===
@app.route("/cron/weekly_report")
def weekly_report():
    if request.args.get("key") != CRON_KEY: abort(403)
    today = datetime.now(timezone(timedelta(hours=9)))
    week_ago = today - timedelta(days=7)
    data = supabase.table("logs").select("*").gte("created_at", week_ago.isoformat()).execute()
    messages = data.data
    total = len(messages)

    types = {}
    for m in messages:
        t = m["type"]
        types[t] = types.get(t, 0) + 1

    report = f"📊【週間レポート】\n配信数：{total}件\n\n"
    for t, count in types.items():
        report += f"・{t}：{count}回\n"
    report += "\n次の週もよろしくね！🌈"

    send_line_message(ADMIN_ID, report)
    log_message_to_supabase(ADMIN_ID, report, "weekly_report")
    return "✅ Weekly report sent"

# === テスト用ルート ===
@app.route("/")
def home():
    return "Kakeru Bot is running! ✅"

# === メイン実行 ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
