import os
import json
import random
import requests
import threading
import time
from datetime import datetime, timedelta
import pytz
import hmac
import hashlib
from flask import Flask, request, abort, jsonify
from supabase import create_client, Client
from openai import OpenAI
import tweepy

# ========================
# Flask / TZ
# ========================
app = Flask(__name__)
TZ = pytz.timezone("Asia/Tokyo")

# ========================
# ENV
# ========================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID", "Uxxxxxxxxx")
CRON_KEY = os.getenv("CRON_KEY")
STORES_SECRET = os.getenv("STORES_SECRET")
STORES_BASE_URL = os.getenv("STORES_BASE_URL", "")
LINE_LINK = os.getenv("LINE_LINK", "")
KAKERU_IMAGE = os.getenv("KAKERU_IMAGE")

TWITTER_API_KEY = os.getenv("TWITTER_API_KEY")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET")

# ========================
# Connections
# ========================
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase接続成功")
except Exception as e:
    print(f"❌ Supabase接続エラー: {e}")
    supabase = None

client = OpenAI(api_key=OPENAI_API_KEY)

def get_twitter_client():
    try:
        return tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_SECRET
        )
    except Exception as e:
        print("❌ Xクライアント初期化失敗:", e)
        return None

# ========================
# Utils
# ========================
def now_iso():
    return datetime.now(TZ).isoformat()

def check_key():
    if request.args.get("key") != CRON_KEY:
        abort(403)

def send_line_message(user_id, text):
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    data = {"to": user_id,
            "messages": [{"type": "text", "text": text[:490]}]}
    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/push", headers=headers, json=data
        )
        print(f"📤 送信({user_id}) → {r.status_code}")
    except Exception as e:
        print("❌ 送信エラー:", e)

def log_message_to_supabase(user_id, message, log_type="auto"):
    if not supabase:
        return
    try:
        supabase.table("logs").insert({
            "user_id": user_id,
            "message": message,
            "type": log_type,
            "created_at": now_iso()
        }).execute()
    except:
        pass

# ========================
# Users
# ========================
def get_user(uid):
    if not supabase:
        return None
    try:
        r = supabase.table("users").select("*").eq("user_id", uid).limit(1).execute()
        return r.data[0] if r.data else None
    except:
        return None

def save_user_profile(uid, **fields):
    if not supabase:
        return
    try:
        existing = get_user(uid) or {}
        data = {**existing, **fields}
        data["user_id"] = uid
        data["updated_at"] = now_iso()
        if not existing.get("created_at"):
            data["created_at"] = now_iso()
            data["last_active"] = now_iso()
        supabase.table("users").upsert(data, on_conflict="user_id").execute()
    except Exception as e:
        print("❌ user保存エラー:", e)

# ========================
# AI Reply
# ========================
def generate_ai_reply(user_id, user_message):
    user = get_user(user_id) or {}
    gender = user.get("gender", "未設定")
    status = user.get("status", "不明")

    system_prompt = (
        f"あなたは恋愛相談AI『カケル』です。\n"
        f"ユーザー属性: 性別={gender}, 状況={status}\n"
        "共感を中心に2〜3文で優しく返信してください。"
    )

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7
        )
        return res.choices[0].message.content.strip()
    except:
        return "少し考えごとしてたみたい、ごめんね。もう一度話してくれる？"


# ========================
# 相談室・Premium・問い合わせ メッセージ
# ========================
def send_soudanshitsu_start(user_id):
    """相談室ボタン → AIに接続（通知なし）"""
    msg = (
        "ご利用ありがとうございます。\n"
        "ここからは『カケル相談室』としてお話を伺います。\n\n"
        "お悩みや気になることを自由に送ってくださいね。"
    )
    send_line_message(user_id, msg)
    log_message_to_supabase(user_id, msg, "system")


def send_premium_notice(user_id):
    """Premiumボタン → Premium準備中メッセージ"""
    msg = (
        "💎Premium は現在準備中です。\n"
        "もう少しお待ちください。"   # ★ここを変更
    )
    send_line_message(user_id, msg)
    log_message_to_supabase(user_id, msg, "system")


def send_inquiry_message(user_id):
    """問い合わせボタン → 管理者へ通知"""
    user = get_user(user_id)
    notify = f"📩【問い合わせ】\nユーザーID: {user_id}\n性別: {user.get('gender')}\n状況: {user.get('status')}"
    send_line_message(ADMIN_ID, notify)

    msg = (
        "お問い合わせありがとうございます。\n"
        "担当より順次ご連絡いたしますので、少しだけお待ちください。"
    )
    send_line_message(user_id, msg)
    log_message_to_supabase(user_id, "問い合わせ受理", "inquiry")


# ========================
# Webhook
# ========================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()
    events = body.get("events", [])

    for event in events:
        if event.get("type") != "message":
            continue
        if event["message"]["type"] != "text":
            continue

        user_id = event["source"]["userId"]
        msg = event["message"]["text"].strip()
        user = get_user(user_id)

        # ▶ 初回登録
        if not user:
            save_user_profile(user_id)
            send_line_message(
                user_id,
                "はじめまして、カケルです。\nまず、性別を教えてね（男性／女性／その他）"
            )
            return "OK"

        # ▶ メニュー処理 ===================
        # Premium（テキストが premium / Premium / premium「準備中」 などでも反応）
        if "premium" in msg.lower():
            send_premium_notice(user_id)
            return "OK"

        if msg == "相談室":
            send_soudanshitsu_start(user_id)
            return "OK"

        if msg == "問い合わせ":
            send_inquiry_message(user_id)
            return "OK"
        # ===================================

        # ▶ 性別登録
        if not user.get("gender"):
            if "男" in msg: gender = "男性"
            elif "女" in msg: gender = "女性"
            else: gender = "その他"
            save_user_profile(user_id, gender=gender)
            send_line_message(user_id, "今の恋の状況を教えてね（片思い／交際中／失恋）")
            return "OK"

        # ▶ 状況登録
        if not user.get("status"):
            if "片" in msg: s = "片思い"
            elif "交" in msg: s = "交際中"
            elif "失" in msg: s = "失恋"
            else: s = "その他"
            save_user_profile(user_id, status=s)
            send_line_message(user_id, "今の気持ちをひとことで教えてね。")
            return "OK"

        # ▶ 最後のプロフィール項目
        if not user.get("feeling"):
            save_user_profile(user_id, feeling=msg)
            send_line_message(user_id, "ありがとう、気持ち大切に受け取ったよ。")
            return "OK"

        # ▶ 相談AI返信
        reply = generate_ai_reply(user_id, msg)
        send_line_message(user_id, reply)
        log_message_to_supabase(user_id, msg, "user")
        log_message_to_supabase(user_id, reply, "ai")

    return "OK"

# ========================
# 定期配信（運勢・曜日メッセージ）
# ========================
@app.route("/cron/omikuji")
def cron_omikuji():
    check_key()
    fortunes = [
        "大吉✨最高の一日になりそう！",
        "中吉😊心穏やかに進めそう。",
        "小吉🍀小さな良いことがあるよ。",
        "吉🌸ゆっくり進んでいこう。",
        "凶💦焦らずチャンスを待ってね。",
    ]
    msg = f"🔮 今日の運勢：{random.choice(fortunes)}"
    broadcast_message(msg)
    return "OK"


@app.route("/cron/monday")
def monday():
    check_key()
    msg = "🌅月曜日：新しい週の始まり。ゆっくりで大丈夫だよ。"
    broadcast_message(msg)
    return "OK"


@app.route("/cron/wednesday")
def wednesday():
    check_key()
    msg = "🌤水曜日：週の折り返し。無理なくいこうね。"
    broadcast_message(msg)
    return "OK"


@app.route("/cron/friday")
def friday():
    check_key()
    msg = "🌙金曜日：一週間お疲れ様。週末は心を休めてね。"
    broadcast_message(msg)
    return "OK"


@app.route("/cron/sunday")
def sunday():
    check_key()
    msg = "☀️日曜日：今週も頑張ったね。自分を労わろう。"
    broadcast_message(msg)
    return "OK"


# ========================
# X（旧Twitter） 自動投稿
# ========================
def generate_ai_post(time_type):
    """朝/夜用の短文メッセージ生成"""
    if time_type == "morning":
        base = "今日もゆっくり、自分のペースで進んでいこうね。"
    else:
        base = "今日はよく頑張ったね。無理しすぎないで、ゆっくり休んでね。"

    prompt = f"恋愛AIカケルとして、以下の内容を含む優しい文章を作成：{base}"

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.7,
            max_tokens=120
        )
        return res.choices[0].message.content.strip()
    except:
        return None


@app.route("/cron/post_tweet")
def post_tweet():
    check_key()
    hour = datetime.now(TZ).hour

    if 5 <= hour < 12:
        t, icon = "morning", "🌤"
    elif 20 <= hour < 24:
        t, icon = "night", "🌙"
    else:
        return jsonify({"status": "skipped"}), 200

    twitter = get_twitter_client()
    if not twitter:
        return "Missing Twitter client", 500

    text = generate_ai_post(t)
    if not text:
        return "Gen error", 500

    final = f"{icon} {text}\n\n登録はこちら👇\n{LINE_LINK}"
    try:
        r = twitter.create_tweet(text=final)
        return jsonify({"status": "ok", "tweet_id": r.data["id"]})
    except Exception as e:
        print("Tweet error:", e)
        return "Error", 500


# ========================
# Keep Alive
# ========================
def keep_alive():
    def loop():
        while True:
            try:
                requests.get("https://kakeru-bot-1.onrender.com/")
            except:
                pass
            time.sleep(600)
    threading.Thread(target=loop, daemon=True).start()


# ========================
# health
# ========================
@app.route("/health")
def health():
    return "OK", 200


@app.route("/")
def home():
    return "🌸 Kakeru Bot running"


# ========================
# Main
# ========================
if __name__ == "__main__":
    keep_alive()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
