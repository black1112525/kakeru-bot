import os
import json
import random
import requests
import threading
import time
from datetime import datetime, timedelta
import pytz
from flask import Flask, request, abort
from supabase import create_client, Client
from openai import OpenAI

# ========================
# Flask アプリ設定
# ========================
app = Flask(__name__)
TZ = pytz.timezone("Asia/Tokyo")

# ========================
# 環境変数
# ========================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID", "Uxxxxxxxxx")
CRON_KEY = os.getenv("CRON_KEY")

# ========================
# Supabase 接続
# ========================
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase接続成功")
except Exception as e:
    print(f"❌ Supabase接続エラー: {e}")
    supabase = None

# ========================
# OpenAI 接続
# ========================
client = OpenAI(api_key=OPENAI_API_KEY)

# ========================
# 共通関数
# ========================
def now_iso():
    return datetime.now(TZ).isoformat()

def check_key():
    if request.args.get("key") != CRON_KEY:
        abort(403)

def send_line_message(user_id: str, text: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {"to": user_id, "messages": [{"type": "text", "text": text[:490]}]}
    try:
        res = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=data)
        print(f"📤 LINE送信ステータス: {res.status_code}")
    except Exception as e:
        print(f"❌ LINE送信エラー: {e}")

def log_message_to_supabase(user_id: str, message: str, log_type: str = "auto"):
    if not supabase:
        return
    try:
        data = {"user_id": user_id, "message": message, "type": log_type, "created_at": now_iso()}
        supabase.table("logs").insert(data).execute()
    except Exception as e:
        print(f"❌ ログ保存エラー: {e}")

# ========================
# ユーザー管理（PostgREST版）
# ========================
def save_user_profile(user_id: str, gender=None, status=None, feeling=None, plan="free"):
    if not supabase:
        print("❌ Supabase未接続。スキップ")
        return
    data = {
        "user_id": user_id,
        "gender": gender,
        "status": status,
        "feeling": feeling,
        "plan": plan,
        "updated_at": now_iso(),
        "created_at": now_iso(),
    }
    try:
        print("💾 upsertデータ:", data)
        res = supabase.postgrest.from_("users").upsert(data, on_conflict=["user_id"]).execute()
        print("✅ Supabase upsert結果:", res)
    except Exception as e:
        print(f"❌ ユーザー保存エラー: {e}")

# ←ここ修正版！
def get_user(user_id: str):
    if not supabase:
        print("❌ Supabase未接続")
        return None
    try:
        res = supabase.postgrest.from_("users").select("*").eq("user_id", user_id).limit(1).execute()

        user_data = None
        if hasattr(res, "data") and res.data:
            user_data = res.data[0]
        elif isinstance(res, dict) and res.get("data"):
            user_data = res["data"][0]

        if user_data:
            print(f"👤 ユーザー取得成功: {user_data}")
        else:
            print(f"⚠️ ユーザー未登録: {user_id}")

        return user_data
    except Exception as e:
        print(f"❌ ユーザー取得エラー: {e}")
        return None

# ========================
# 会話履歴取得
# ========================
def get_recent_conversation(user_id: str, limit=10):
    if not supabase:
        return []
    try:
        res = supabase.table("logs").select("message, type").eq("user_id", user_id).order("created_at", desc=True).limit(limit).execute()
        logs = res.data or []
        convo = []
        for l in logs[::-1]:
            if l["type"] == "user":
                convo.append({"role": "user", "content": l["message"]})
            elif l["type"] == "ai":
                convo.append({"role": "assistant", "content": l["message"]})
        return convo
    except Exception as e:
        print(f"❌ 会話履歴取得エラー: {e}")
        return []

# ========================
# 入力正規化
# ========================
def normalize_gender(text: str):
    t = text.strip().lower()
    if "男" in t: return "男性"
    if "女" in t: return "女性"
    if "他" in t: return "その他"
    return None

def normalize_status(text: str):
    t = text.strip()
    if "片" in t: return "片思い"
    if "交" in t: return "交際中"
    if "失" in t: return "失恋"
    return "その他"

# ========================
# AI返信生成
# ========================
def generate_ai_reply(user_id: str, user_message: str):
    user = get_user(user_id) or {}
    gender = user.get("gender") or "未設定"
    status = user.get("status") or "不明"

    system_prompt = (
        f"あなたは『カケル』という優しい恋愛相談AIです。\n"
        f"ユーザー属性: 性別={gender}, 状況={status}\n"
        "相手に寄り添い、安心できる言葉で2〜4文で返答してください。"
    )

    history = get_recent_conversation(user_id)
    messages = [{"role": "system", "content": system_prompt}] + history
    messages.append({"role": "user", "content": user_message})

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.8,
            timeout=40,
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ OpenAI応答エラー: {e}")
        return "ごめんね、少し考えすぎちゃったみたい。もう一度話してくれる？"

# ========================
# Webhook
# ========================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()
    events = body.get("events", [])
    for event in events:
        if event["type"] == "message" and event["message"]["type"] == "text":
            user_id = event["source"]["userId"]
            user_message = event["message"]["text"].strip()
            print(f"📩 {user_id}: {user_message}")

            user = get_user(user_id)
            if not user:
                save_user_profile(user_id)
                send_line_message(user_id, "はじめまして、カケルです。\nまず、性別を教えてください（男性／女性／その他）")
                continue

            if not user.get("gender"):
                g = normalize_gender(user_message)
                if g:
                    save_user_profile(user_id, gender=g)
                    send_line_message(user_id, "ありがとう😊 次に、今の恋の状況を教えてください（片思い／交際中／失恋／その他）")
                else:
                    send_line_message(user_id, "ごめん、もう一度だけ！性別を教えてね（男性／女性／その他）")
                continue

            if not user.get("status"):
                s = normalize_status(user_message)
                if s:
                    save_user_profile(user_id, status=s)
                    send_line_message(user_id, "なるほど…！\n最後に、今の気持ちをひとことで教えてください（例：寂しい・モヤモヤ・楽しいなど）")
                else:
                    send_line_message(user_id, "状況を教えてね（片思い／交際中／失恋／その他）")
                continue

            if not user.get("feeling"):
                save_user_profile(user_id, feeling=user_message[:120])
                send_line_message(user_id, "ありがとう。あなたの気持ち、大切に受け取ったよ。これから一緒に考えていこう。")
                continue

            reply = generate_ai_reply(user_id, user_message)
            send_line_message(user_id, reply)
            log_message_to_supabase(user_id, user_message, "user")
            log_message_to_supabase(user_id, reply, "ai")
    return "OK"

# ========================
# デバッグ用ルート
# ========================
@app.route("/debug/test_upsert")
def debug_test_upsert():
    check_key()
    uid = request.args.get("uid", "TEST_USER")
    save_user_profile(uid, gender="男性", status="交際中", feeling="テストOK")
    return f"upsert sent for {uid}"

@app.route("/debug/get_user")
def debug_get_user():
    check_key()
    uid = request.args.get("uid", "TEST_USER")
    u = get_user(uid)
    return json.dumps(u or {}, ensure_ascii=False)

# ========================
# 定期配信（月・水・金・日）
# ========================
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

# ========================
# Render スリープ防止
# ========================
def keep_alive():
    def ping():
        while True:
            try:
                requests.get("https://kakeru-bot-1.onrender.com/")
                print("💤 Keep-alive ping")
            except Exception as e:
                print(f"Keep-alive error: {e}")
            time.sleep(600)
    threading.Thread(target=ping, daemon=True).start()

# ========================
# ヘルスチェック
# ========================
@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def home():
    return "🌸 Kakeru Bot running gently with memory & cron!"

# ========================
# メイン実行
# ========================
if __name__ == "__main__":
    keep_alive()
    app.run(host="0.0.0.0", port=10000)
