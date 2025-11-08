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

# ========================
# Flask アプリ
# ========================
app = Flask(__name__)
TZ = pytz.timezone("Asia/Tokyo")

# ========================
# 環境変数
# ========================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID", "Uxxxxxxxx")  # 管理者LINE ID
CRON_KEY = os.getenv("CRON_KEY")

# ========================
# Supabase 接続
# ========================
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized successfully")
except Exception as e:
    print(f"❌ Supabase connection error: {e}")
    supabase = None

# ========================
# OpenAI
# ========================
client = OpenAI(api_key=OPENAI_API_KEY)

# ========================
# 共通ユーティリティ
# ========================
def now_iso():
    return datetime.now(TZ).isoformat()

def send_line_message(user_id: str, text: str):
    """テキストをプッシュ送信（最大490文字に制限）"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {"to": user_id, "messages": [{"type": "text", "text": text[:490]}]}
    try:
        res = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=data)
        print(f"📤 LINE送信: {res.status_code}")
    except Exception as e:
        print(f"❌ LINE送信エラー: {e}")

def log_message_to_supabase(user_id: str, message: str, log_type: str = "auto"):
    """会話ログ保存（logs テーブル）"""
    if not supabase:
        print("⚠️ Supabase未接続。ログは保存されません。")
        return
    try:
        data = {
            "user_id": user_id,
            "message": message,
            "type": log_type,
            "created_at": now_iso(),
        }
        supabase.table("logs").insert(data).execute()
        print(f"🗂 ログ保存: {log_type}")
    except Exception as e:
        print(f"❌ ログ保存エラー: {e}")

# ========================
# users テーブル操作
# ========================
def save_user_profile(user_id: str, gender=None, status=None, feeling=None, plan="free"):
    """ユーザー基本情報を upsert（user_id 主キー前提）"""
    if not supabase:
        print("⚠️ Supabase未接続。ユーザーデータは保存されません。")
        return
    try:
        data = {
            "user_id": user_id,
            "gender": gender,
            "status": status,
            "feeling": feeling,
            "plan": plan,
            "updated_at": now_iso(),
            "created_at": now_iso(),
        }
        # 主キー user_id で upsert
        supabase.table("users").upsert(data, on_conflict=["user_id"]).execute()
        print(f"🧍ユーザーデータ保存: {user_id}")
    except Exception as e:
        print(f"❌ ユーザー保存エラー: {e}")

def get_user(user_id: str):
    """users から1件取得（なければ None）"""
    try:
        res = supabase.table("users").select("*").eq("user_id", user_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ ユーザー取得エラー: {e}")
        return None

# ========================
# 会話履歴取得（logs）
# ========================
def get_recent_conversation(user_id, limit=10):
    if not supabase:
        return []
    try:
        res = supabase.table("logs").select("message, type") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(limit).execute()
        logs = res.data[::-1]
        convo = []
        for l in logs:
            if l["type"] == "user":
                convo.append({"role": "user", "content": l["message"]})
            elif l["type"] == "ai":
                convo.append({"role": "assistant", "content": l["message"]})
        return convo
    except Exception as e:
        print(f"⚠️ 会話履歴取得エラー: {e}")
        return []

# ========================
# 正規化ヘルパ
# ========================
def normalize_gender(text: str):
    t = text.strip().lower()
    if "男" in t: return "男性"
    if "女" in t: return "女性"
    if "その他" in t or "ほか" in t or "他" in t: return "その他"
    return None

def normalize_status(text: str):
    t = text.strip()
    candidates = ["片思い", "片想い", "交際中", "彼女あり", "彼氏あり", "失恋", "その他"]
    for c in candidates:
        if c in t:
            return "片思い" if c in ["片思い", "片想い"] else ("交際中" if c in ["交際中","彼女あり","彼氏あり"] else ("失恋" if c=="失恋" else "その他"))
    return None

# ========================
# AI 返信生成
# ========================
def generate_ai_reply(user_id, user_message):
    user = get_user(user_id)
    gender = (user or {}).get("gender") or "未設定"
    status = (user or {}).get("status") or "不明"

    system_prompt = (
        f"あなたは『カケル』という誠実で優しい恋愛相談員です。\n"
        f"ユーザー属性: 性別={gender} / 状況={status}\n"
        "相手の気持ちを受け止め、共感を伝え、安心できる言葉を返してください。\n"
        "丁寧な言葉遣いで2〜4文にまとめてください。"
    )

    history = get_recent_conversation(user_id, limit=10)
    messages = [{"role": "system", "content": system_prompt}] + history
    messages.append({"role": "user", "content": user_message})

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.8,
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ OpenAI返答エラー: {e}")
        return "ごめんなさい、少し考え込んでしまいました。もう一度話してもらえますか？"

# ========================
# 認証チェック（CRON用）
# ========================
def check_key():
    if request.args.get("key") != CRON_KEY:
        abort(403)

# ========================
# Webhook（質問フロー統合）
# ========================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()
    events = body.get("events", [])

    for event in events:
        if event.get("type") == "message" and event["message"].get("type") == "text":
            user_id = event["source"]["userId"]
            user_message = event["message"]["text"].strip()
            print(f"💬 {user_id}: {user_message}")

            # 1) 初回判定
            user = get_user(user_id)
            if not user:
                save_user_profile(user_id)  # 空で作成
                send_line_message(
                    user_id,
                    "はじめまして、カケルです。\nあなたの恋の状況を少し教えてください。\nまず、性別を教えてください（男性／女性／その他）"
                )
                # 初回はここで終了（次の発言から続き）
                continue

            # 2) 未登録の項目を順に聞く
            if not user.get("gender"):
                g = normalize_gender(user_message)
                if g:
                    save_user_profile(user_id, gender=g)
                    send_line_message(user_id, "ありがとう😊\n次に、今の恋の状況を教えてください（片思い／交際中／失恋／その他）")
                else:
                    send_line_message(user_id, "ごめん、もう一度だけ！\n性別を教えてね（男性／女性／その他）")
                continue

            user = get_user(user_id)  # 更新反映
            if not user.get("status"):
                s = normalize_status(user_message)
                if s:
                    save_user_profile(user_id, status=s)
                    send_line_message(user_id, "なるほど…！\n最後に、今の気持ちをひとことで教えてください（例：寂しい・モヤモヤ・楽しい など）")
                else:
                    send_line_message(user_id, "わかった。状況はどれに近い？（片思い／交際中／失恋／その他）")
                continue

            user = get_user(user_id)
            if not user.get("feeling"):
                # なんでも受け入れて保存
                save_user_profile(user_id, feeling=user_message[:120])
                send_line_message(user_id, "ありがとう。あなたの気持ち、大切に受け取ったよ。\nこれから一緒に考えていこう。")
                continue

            # 3) 通常会話モード
            reply = generate_ai_reply(user_id, user_message)
            send_line_message(user_id, reply)
            log_message_to_supabase(user_id, user_message, "user")
            log_message_to_supabase(user_id, reply, "ai")

    return "OK"

# ========================
# 定期配信
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

# ========================
# 週次レポート
# ========================
@app.route("/cron/weekly_report")
def weekly_report():
    check_key()
    try:
        now = datetime.now(TZ)
        start = now - timedelta(days=7)
        res = supabase.table("logs").select("*").gte("created_at", start.isoformat()).execute()
        logs = res.data or []

        report = "📊【カケル週報】\n"
        report += f"記録件数：{len(logs)}件\n"
        ai_count = sum(1 for l in logs if l.get("type") == "ai")
        report += f"AI返信数：{ai_count}件\n"

        # 120字以内の要約（トークン節約のため一部のみ）
        mini = json.dumps(logs[:200], ensure_ascii=False)[:3000]
        ai_summary = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "あなたは恋愛相談AI『カケル』の運用アシスタントです。"},
                {"role": "user", "content": "以下は今週の会話ログです。主要な相談テーマを3点以内、運用改善提案を2点、合計120字以内で要約して。\n" + mini}
            ],
            temperature=0.6,
            max_tokens=160,
        )
        summary = ai_summary.choices[0].message.content.strip()
        report += "\n🧠【AI分析】\n" + summary

        send_line_message(ADMIN_ID, report[:490])
        log_message_to_supabase(ADMIN_ID, report, "weekly_report")
        return "✅ Weekly report sent"
    except Exception as e:
        print(f"❌ Weekly report error: {e}")
        return str(e)

# ========================
# Render スリープ防止
# ========================
def keep_alive():
    def ping():
        while True:
            try:
                requests.get("https://kakeru-bot-1.onrender.com/")
                print("🔁 Keep-alive ping")
            except Exception as e:
                print(f"⚠️ Keep-alive error: {e}")
            time.sleep(600)
    thread = threading.Thread(target=ping, daemon=True)
    thread.start()

# ========================
# ヘルスチェック
# ========================
@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def home():
    return "🌸 Kakeru Bot running gently with memory!"

# ========================
# メイン実行
# ========================
if __name__ == "__main__":
    keep_alive()
    app.run(host="0.0.0.0", port=10000)
