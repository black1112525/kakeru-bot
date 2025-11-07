# main.py
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
import openai

# ===== Flask初期化 =====
app = Flask(__name__)

# ===== 環境変数 =====
ADMIN_ID = os.getenv("ADMIN_ID")
CRON_KEY = os.getenv("CRON_KEY", "yukito")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

# ===== Supabase接続 =====
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized successfully")
except Exception as e:
    print(f"❌ Supabase connection error: {e}")
    supabase = None

# ===== LINE送信 =====
def send_line_message(user_id, text):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    data = {"to": user_id, "messages": [{"type": "text", "text": text[:490]}]}
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

# ===== AI返信生成（丁寧・親しみトーン＋履歴保持） =====
def generate_ai_reply(user_id, user_message):
    system_prompt = (
        "あなたは『カケル』という男性向け恋愛相談AIです。\n"
        "トーンは丁寧で優しく、親しみやすい話し方にしてください。\n"
        "相手の気持ちをまず受け止め、共感を伝え、その後で前向きなアドバイスを1つだけ添えます。\n"
        "2〜3文でまとめ、相手を安心させる言葉を選んでください。\n"
        "一人称は使わず自然な敬語でOKです。\n"
        "例：『それはつらかったですよね。でも大丈夫、少しずつでいいですよ。』\n"
        "　　『焦る気持ち、分かりますよ。無理せずいきましょうね。』"
    )

    # 履歴取得（30件）
    try:
        res = supabase.table("logs").select("type, message")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(30)\
            .execute()
        logs = list(reversed(res.data))
    except Exception as e:
        print(f"⚠️ 会話履歴取得エラー: {e}")
        logs = []

    messages = [{"role": "system", "content": system_prompt}]
    for log in logs:
        role = "assistant" if log["type"] == "ai" else "user"
        messages.append({"role": role, "content": log["message"]})
    messages.append({"role": "user", "content": user_message})

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.85,
        )
        reply = response.choices[0].message.content.strip()
        return reply
    except Exception as e:
        print(f"❌ OpenAI返信エラー: {e}")
        return "ごめんなさい、少し考え込んでしまいました。もう一度話してもらえますか？"

# ===== LINE Webhook受信 =====
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()
    events = body.get("events", [])

    for event in events:
        if event["type"] == "message" and event["message"]["type"] == "text":
            user_id = event["source"]["userId"]
            user_message = event["message"]["text"]
            print(f"💬 受信: {user_id} - {user_message}")

            reply = generate_ai_reply(user_id, user_message)
            send_line_message(user_id, reply)

            log_message_to_supabase(user_id, user_message, "user")
            log_message_to_supabase(user_id, reply, "ai")

    return "OK"

# ===== 定期配信 =====
@app.route("/cron/monday")
def monday():
    check_key()
    msg = "🌅月曜メッセージ：新しい週の始まり。無理せず少しずつ進もう。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "monday")
    return "✅ Monday sent"

@app.route("/cron/wednesday")
def wednesday():
    check_key()
    msg = "🌿水曜メッセージ：週の折り返し。焦らずリズムを整えてね。"
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
    msg = "☕日曜メッセージ：今週もよく頑張りましたね。感謝してリセットしよう。"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "sunday")
    return "✅ Sunday sent"

@app.route("/cron/omikuji")
def omikuji():
    check_key()
    fortunes = [
        "🌞大吉：最高の一日になりそうです！",
        "🍀中吉：いい流れが来てますよ。",
        "🌸小吉：穏やかな日になりますように。",
        "🌾吉：小さな幸せを大事にしましょう。",
        "🌧凶：今日は自分を労わる日です。"
    ]
    msg = f"🎯おはようございます！今日の運勢は…\n{random.choice(fortunes)}"
    send_line_message(ADMIN_ID, msg)
    log_message_to_supabase(ADMIN_ID, msg, "omikuji")
    return "✅ Omikuji sent"

@app.route("/cron/moon_auto")
def moon_auto():
    check_key()
    now = datetime.now(pytz.timezone("Asia/Tokyo"))
    moon = ephem.Moon()
    moon.compute(now)
    moon_age = moon.phase
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
            report += f"記録総数：{total}件\n\n"
            type_count = {}
            for log in logs:
                t = log["type"]
                type_count[t] = type_count.get(t, 0) + 1
            for t, c in type_count.items():
                report += f"{t}：{c}回\n"

            analysis_prompt = (
                "以下は過去1週間のLINE Botのログデータです。"
                "全体傾向を簡潔にまとめ、運用改善のヒントを優しく提案してください。"
                f"タイプ別件数: {type_count}\n"
            )

            ai_res = openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "あなたは恋愛相談AI『カケル』の運用アシスタントです。"},
                    {"role": "user", "content": analysis_prompt}
                ]
            )
            ai_summary = ai_res.choices[0].message.content.strip()
            report += "\n🧠【AI分析】\n" + ai_summary
            report += "\n\n🌙来週もよろしくお願いします！"

        send_line_message(ADMIN_ID, report[:490])
        log_message_to_supabase(ADMIN_ID, report, "weekly_report")
        return "✅ Weekly report sent"
    except Exception as e:
        print(f"❌ Weekly report error: {e}")
        return str(e)

# ===== Renderスリープ防止 =====
def keep_alive():
    def ping():
        while True:
            try:
                requests.get("https://kakeru-bot-1.onrender.com/")
                print("💤 Ping sent to keep Render awake")
            except Exception as e:
                print(f"⚠️ Keep-alive ping error: {e}")
            time.sleep(600)
    thread = threading.Thread(target=ping)
    thread.daemon = True
    thread.start()

# ===== 動作確認 =====
@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def home():
    return "✅ Kakeru Bot running gently with memory!"

# ===== メイン実行 =====
if __name__ == "__main__":
    keep_alive()
    app.run(host="0.0.0.0", port=10000)
