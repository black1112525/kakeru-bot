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
import tweepy  # X(旧Twitter) 自動投稿

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
STORES_SECRET = os.getenv("STORES_SECRET")  # STORES署名検証用
STORES_BASE_URL = os.getenv("STORES_BASE_URL", "https://your-stores-link.com/?line_user_id=")  # 決済リンク
LINE_LINK = os.getenv("LINE_LINK", "https://lin.ee/xxxxxx")  # X投稿の誘導リンク
KAKERU_IMAGE = os.getenv("KAKERU_IMAGE")  # 画像URL（v2の都合で未添付運用）

# X(Twitter) API（後でキーを入れれば動く）
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
    """X APIクライアント（v2）"""
    try:
        return tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_SECRET
        )
    except Exception as e:
        print(f"❌ Xクライアント初期化失敗: {e}")
        return None

# ========================
# Utils
# ========================
def now_iso():
    return datetime.now(TZ).isoformat()

def check_key():
    if request.args.get("key") != CRON_KEY:
        abort(403)

def send_line_message(user_id: str, text: str):
    """LINEプッシュ送信（テキスト）"""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    data = {"to": user_id, "messages": [{"type": "text", "text": text[:490]}]}
    try:
        res = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=data)
        print(f"📤 LINE送信({user_id}) → {res.status_code}")
    except Exception as e:
        print(f"❌ LINE送信エラー({user_id}): {e}")

def send_flex(user_id: str, flex_contents: dict, alt_text="メッセージ"):
    """LINEプッシュ送信（Flex）"""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    data = {"to": user_id, "messages": [{"type": "flex", "altText": alt_text, "contents": flex_contents}]}
    try:
        res = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=data)
        print(f"📤 LINE Flex送信({user_id}) → {res.status_code}")
    except Exception as e:
        print(f"❌ LINE Flex送信エラー({user_id}): {e}")

def log_message_to_supabase(user_id: str, message: str, log_type: str = "auto"):
    """会話ログ保存＋Premiumならlast_active更新"""
    if not supabase:
        return
    try:
        now = now_iso()
        data = {"user_id": user_id, "message": message, "type": log_type, "created_at": now}
        supabase.table("logs").insert(data).execute()

        if user_id not in ("system", "admin"):
            user = get_user(user_id)
            if user and user.get("plan") == "premium":
                supabase.table("users").update({"last_active": now}).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"❌ ログ保存エラー: {e}")

def verify_signature(req):
    """STORES Webhook署名検証"""
    if not STORES_SECRET:
        return True
    received = req.headers.get("X-Stores-Signature", "")
    computed = hmac.new(STORES_SECRET.encode(), req.data, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received, computed)

# ========================
# Broadcast
# ========================
def broadcast_message(msg: str, premium_only: bool = False):
    """登録済み（条件付き）全ユーザーに送信"""
    if not supabase:
        print("❌ Supabase未接続。送信中止。")
        return
    try:
        query = supabase.table("users").select("user_id, plan")
        if premium_only:
            query = query.eq("plan", "premium")
        res = query.execute()
        users = res.data or []
        print(f"📡 {'Premium' if premium_only else '全'}ユーザー送信: {len(users)}人")
        for u in users:
            uid = u.get("user_id")
            if uid:
                send_line_message(uid, msg)
                time.sleep(0.3)
        print("✅ 送信完了")
    except Exception as e:
        print(f"❌ 全体送信エラー: {e}")

# ========================
# Users
# ========================
def get_user(user_id: str):
    if not supabase:
        return None
    try:
        res = supabase.table("users").select("*").eq("user_id", user_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"❌ ユーザー取得エラー: {e}")
        return None

def save_user_profile(user_id: str, gender=None, status=None, feeling=None, plan=None):
    """既存値とマージ保存（on_conflict修正版）"""
    if not supabase:
        print("❌ Supabase未接続")
        return
    try:
        existing = get_user(user_id) or {}
        data = {
            "user_id": user_id,
            "gender": gender if gender is not None else existing.get("gender"),
            "status": status if status is not None else existing.get("status"),
            "feeling": feeling if feeling is not None else existing.get("feeling"),
            "plan": plan if plan is not None else existing.get("plan", "free"),
            "updated_at": now_iso(),
            "created_at": existing.get("created_at", now_iso()),
            "last_active": existing.get("last_active") or now_iso(),
        }
        supabase.table("users").upsert(data, on_conflict="user_id").execute()
        print(f"💾 ユーザーデータ保存: {data}")
    except Exception as e:
        print(f"❌ ユーザー保存エラー: {e}")

# ========================
# Normalizers
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
# Conversation helpers
# ========================
def get_conversation_count(user_id: str):
    """ユーザーの発話回数（userタイプのログ件数）"""
    if not supabase:
        return 0
    res = supabase.table("logs").select("id").eq("user_id", user_id).eq("type", "user").execute()
    return len(res.data or [])

# ========================
# Premium誘導（Flex）
# ========================
def send_premium_invite(user_id: str):
    """無料ユーザーにPremium登録を案内するFlexメッセージ"""
    link = f"{STORES_BASE_URL}{user_id}"
    bubble = {
        "type": "bubble",
        "hero": {
            "type": "image",
            "url": "https://cdn.pixabay.com/photo/2017/01/06/20/19/diamond-1950120_1280.jpg",
            "size": "full", "aspectRatio": "16:9", "aspectMode": "cover"
        },
        "body": {
            "type": "box", "layout": "vertical", "contents": [
                {"type": "text", "text": "💎 カケル Premium", "weight": "bold", "size": "xl"},
                {"type": "text", "text": "より深く、心に寄り添う特別な時間を。", "wrap": True, "margin": "md"},
                {"type": "separator", "margin": "md"},
                {"type": "text", "text": "✨ 特典：\n・心理分析つきAI返信\n・安心の毎日メッセージ\n・（オプション）日記サポート", "wrap": True, "margin": "md"},
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical", "contents": [
                {"type": "button", "style": "primary", "color": "#A16AE8",
                 "action": {"type": "uri", "label": "💎 Premiumをはじめる！", "uri": link}}]
        }
    }
    send_flex(user_id, bubble, alt_text="Premiumのご案内")

def send_premium_menu(user_id: str, plan: str):
    """Premiumユーザー向けのミニメニュー（Flex）/ 無料は運勢のみ"""
    buttons = []
    if plan == "premium":
        buttons.append({"type": "button", "style": "primary",
                        "action": {"type": "message", "label": "💌 今日の想いを書く", "text": "/diary"}})
    buttons.append({"type": "button", "style": "secondary",
                    "action": {"type": "message", "label": "🔮 今日の運勢を見る", "text": "/omikuji"}})

    bubble = {
        "type": "bubble",
        "body": {
            "type": "box", "layout": "vertical", "contents": [
                {"type": "text", "text": "💎 Premiumメニュー", "weight": "bold", "size": "lg"},
                *buttons
            ]
        }
    }
    send_flex(user_id, bubble, alt_text="Premiumメニュー")

# ========================
# AI Reply
# ========================
def generate_ai_reply(user_id: str, user_message: str):
    user = get_user(user_id) or {}
    plan = user.get("plan", "free")
    gender = user.get("gender") or "未設定"
    status = user.get("status") or "不明"

    if plan == "premium":
        system_prompt = (
            f"あなたは恋愛心理AI『カケルPremium』です。\n"
            f"ユーザー属性: 性別={gender}, 状況={status}\n"
            "心理的洞察を交え、相手を安心させる言葉で5〜6文で返信してください。"
        )
    else:
        system_prompt = (
            f"あなたは恋愛相談AI『カケル』です。\n"
            f"ユーザー属性: 性別={gender}, 状況={status}\n"
            "共感を中心に2〜3文で優しく返信してください。"
        )

    history = []
    try:
        res = supabase.table("logs").select("message, type").eq("user_id", user_id).order("created_at", desc=True).limit(10).execute()
        for l in (res.data or [])[::-1]:
            if l["type"] == "user":
                history.append({"role": "user", "content": l["message"]})
            elif l["type"] == "ai":
                history.append({"role": "assistant", "content": l["message"]})
    except Exception as e:
        print(f"❌ 履歴取得エラー: {e}")

    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
    try:
        res = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0.8)
        return res.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ OpenAI応答エラー: {e}")
        return "ごめんね、少し考えすぎちゃったみたい。もう一度話してくれる？"

# ========================
# LINE Webhook（チャット本体）
# ========================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()
    events = body.get("events", [])
    for event in events:
        if event.get("type") == "message" and event["message"]["type"] == "text":
            user_id = event["source"]["userId"]
            user_message = event["message"]["text"].strip()
            print(f"📩 {user_id}: {user_message}")

            user = get_user(user_id)
            if not user:
                save_user_profile(user_id)
                send_line_message(user_id, "はじめまして、カケルです🌸\nまず、性別を教えてね（男性／女性／その他）")
                continue

            if user_message in ["/menu", "メニュー", "menu"]:
                send_premium_menu(user_id, user.get("plan", "free"))
                continue

            if user_message in ["プレミアム", "premium", "有料", "課金"]:
                link = f"{STORES_BASE_URL}{user_id}"
                send_line_message(user_id, f"💎プレミアム登録はこちらから✨\n{link}")
                continue

            if user_message in ["/omikuji", "おみくじ", "今日の運勢"]:
                fortunes = [
                    "大吉✨最高の一日になりそう！",
                    "中吉😊穏やかな幸せが訪れそう。",
                    "小吉🍀小さな幸運を見逃さないでね。",
                    "吉🌸努力が実る兆し。",
                    "凶💦焦らずチャンスを待とう。"
                ]
                msg = f"🔮 今日の運勢：{random.choice(fortunes)}"
                send_line_message(user_id, msg)
                log_message_to_supabase(user_id, msg, "omikuji")
                continue

            if user_message in ["/diary", "今日の想い", "気持ちメモ"]:
                if user.get("plan") == "premium":
                    send_line_message(user_id, "🩵 今日の気持ちを教えて。どんなことでも大丈夫だよ。")
                    log_message_to_supabase(user_id, "__diary_wait__", "system")
                else:
                    send_premium_invite(user_id)
                continue

            if not user.get("gender"):
                g = normalize_gender(user_message)
                if g:
                    save_user_profile(user_id, gender=g)
                    send_line_message(user_id, "ありがとう😊 次に、今の恋の状況を教えてね（片思い／交際中／失恋／その他）")
                else:
                    send_line_message(user_id, "ごめん、もう一度だけ！性別を教えてね（男性／女性／その他）")
                continue

            if not user.get("status"):
                s = normalize_status(user_message)
                if s:
                    save_user_profile(user_id, status=s)
                    send_line_message(user_id, "なるほど…！\n最後に、今の気持ちをひとことで教えて（例：寂しい・モヤモヤ・楽しいなど）")
                else:
                    send_line_message(user_id, "状況を教えてね（片思い／交際中／失恋／その他）")
                continue

            if not user.get("feeling"):
                save_user_profile(user_id, feeling=user_message[:120])
                send_line_message(user_id, "ありがとう。あなたの気持ち、大切に受け取ったよ。これから一緒に考えていこう。")
                continue

            diary_wait = False
            try:
                r = supabase.table("logs").select("message, type").eq("user_id", user_id).order("created_at", desc=True).limit(3).execute()
                for row in r.data or []:
                    if row.get("type") == "system" and row.get("message") == "__diary_wait__":
                        diary_wait = True
                        break
            except Exception as e:
                print("diary_wait判定エラー:", e)

            if diary_wait and user.get("plan") == "premium":
                saved = f"📝『今日の想い』を記録したよ。\n— {user_message[:200]}"
                log_message_to_supabase(user_id, f"[DIARY]{user_message[:1000]}", "diary")
                send_line_message(user_id, "ありがとう、ちゃんと受け取ったよ🫶\n少しずつ気持ちを整えていこうね。")
                log_message_to_supabase(user_id, saved, "ai")
                log_message_to_supabase(user_id, "__diary_end__", "system")
                continue

            reply = generate_ai_reply(user_id, user_message)
            send_line_message(user_id, reply)
            log_message_to_supabase(user_id, user_message, "user")
            log_message_to_supabase(user_id, reply, "ai")

            if get_conversation_count(user_id) % 5 == 0 and user.get("plan") != "premium":
                send_premium_invite(user_id)

    return "OK"

# ========================
# STORES Webhook（決済/解約）
# ========================
@app.route("/payment/webhook", methods=["POST"])
def payment_webhook():
    if not verify_signature(request):
        abort(403)
    try:
        data = request.get_json()
        event_type = data.get("event", "")
        user_id = data.get("user_id") or (data.get("metadata") or {}).get("line_user_id")

        if not user_id:
            print("❌ user_idがWebhookに含まれていません")
            return "NG", 400

        if event_type in ["payment.success", "subscription.created"]:
            save_user_profile(user_id, plan="premium")
            send_line_message(user_id, "✨Premium登録ありがとう！\nこれからは、もっと深く寄り添っていくね💎")
            log_message_to_supabase(user_id, "プレミアム登録完了", "system")
            return "OK", 200

        elif event_type in ["subscription.canceled", "payment.canceled"]:
            save_user_profile(user_id, plan="free")
            send_line_message(user_id, "💡Premiumを解除しました。また戻りたくなったら、いつでも待ってるね。")
            log_message_to_supabase(user_id, "プレミアム解約", "system")
            return "OK", 200

        else:
            print(f"🌀 未対応イベント: {event_type}")
            return "Ignored", 200

    except Exception as e:
        print(f"❌ Webhook処理エラー: {e}")
        return str(e), 500

# ========================
# 定期配信（月・水・金・日・今日の運勢）
# ========================
@app.route("/cron/monday")
def monday():
    check_key()
    msg = "🌅月曜メッセージ：新しい週の始まり、焦らず少しずつ進もう。"
    broadcast_message(msg)
    log_message_to_supabase("system", msg, "monday")
    return "✅ Monday broadcast sent"

@app.route("/cron/wednesday")
def wednesday():
    check_key()
    msg = "🌤水曜メッセージ：週の折り返し、リズムを整えてね。"
    broadcast_message(msg)
    log_message_to_supabase("system", msg, "wednesday")
    return "✅ Wednesday broadcast sent"

@app.route("/cron/friday")
def friday():
    check_key()
    msg = "🌙金曜メッセージ：1週間お疲れさま。今夜は少し、自分の気持ちを労わってね。"
    broadcast_message(msg)
    follow = "💭 今週のこと、少し整理してみない？\nPremiumなら丁寧に寄り添うメッセージで支えるよ💌"
    broadcast_message(follow)
    try:
        res = supabase.table("users").select("user_id, plan").eq("plan", "free").execute()
        for u in res.data or []:
            send_premium_invite(u["user_id"])
    except Exception as e:
        print("金曜誘導エラー:", e)
    log_message_to_supabase("system", msg + "\n" + follow, "friday")
    return "✅ Friday broadcast sent"

@app.route("/cron/sunday")
def sunday():
    check_key()
    msg = "☀️日曜メッセージ：今週もよく頑張りました。自分に優しく、心をリセットしよう。"
    broadcast_message(msg)
    log_message_to_supabase("system", msg, "sunday")
    return "✅ Sunday broadcast sent"

@app.route("/cron/omikuji")
def cron_omikuji():
    """毎日配信：今日の運勢（文言統一済）"""
    check_key()
    fortunes = [
        "大吉✨最高の一日になりそう！",
        "中吉😊穏やかな幸せが訪れそう。",
        "小吉🍀小さな幸運を見逃さないでね。",
        "吉🌸努力が実る兆し。",
        "凶💦焦らずチャンスを待とう。"
    ]
    msg = f"🔮 今日の運勢：{random.choice(fortunes)}"
    broadcast_message(msg)
    log_message_to_supabase("system", msg, "omikuji")
    return "✅ Omikuji broadcast sent"

# ========================
# 週次レポート（管理者専用）
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
# Premiumチェックイン（12時間無会話なら20時に送る）
# ========================
@app.route("/cron/premium_check_inactive")
def premium_check_inactive():
    check_key()
    try:
        now = datetime.now(TZ)
        threshold = now - timedelta(hours=12)

        res = supabase.table("users").select("user_id, plan, last_active").eq("plan", "premium").execute()
        users = res.data or []
        target = []
        for u in users:
            la = u.get("last_active")
            if not la:
                target.append(u); continue
            try:
                la_dt = datetime.fromisoformat(la)
            except Exception:
                target.append(u); continue
            if la_dt <= threshold:
                target.append(u)

        msg_pool = [
            "🌙こんばんは、今日も一日お疲れさま。話したいこと、あったらいつでも聞かせてね。",
            "💭最近どうしてるかな？気持ち、ひとりで抱え込まなくて大丈夫だよ。",
            "🫶 無理しないでね。君のペースで大丈夫。いつでもここにいるよ。",
            "🌸 今日は少しでも穏やかな時間が過ごせていますように。"
        ]
        msg = random.choice(msg_pool)

        for u in target:
            uid = u["user_id"]
            send_line_message(uid, msg)
            log_message_to_supabase(uid, msg, "check_in")
            time.sleep(0.3)

        print(f"✅ Premiumチェックイン送信: {len(target)}人")
        return "✅ Premium check-in sent"
    except Exception as e:
        print(f"❌ Premiumチェック配信エラー: {e}")
        return str(e)

# ========================
# 🐦 X 自動投稿（朝夜）＋自己学習
# ========================
def get_trend_feedback():
    """過去投稿の傾向をAIに渡す（最新10件）"""
    if not supabase:
        return "過去投稿データなし。"
    try:
        res = supabase.table("posts").select("text, likes, retweets").order("created_at", desc=True).limit(10).execute()
        posts = res.data or []
        if not posts:
            return "過去投稿データなし。"
        avg_like = sum(p.get("likes", 0) for p in posts) / len(posts)
        avg_rt = sum(p.get("retweets", 0) for p in posts) / len(posts)
        top = max(posts, key=lambda p: p.get("likes", 0) + p.get("retweets", 0))
        return f"最近の平均いいねは{avg_like:.1f}、RTは{avg_rt:.1f}。最も反応が良かった投稿例：『{top['text'][:80]}…』"
    except Exception:
        return "傾向取得に失敗。"

def generate_ai_post(time_type):
    """反応傾向を少し反映しながら生成"""
    feedback = get_trend_feedback()
    if time_type == "morning":
        mood = "朝のあいさつを含み、前向きで温かい恋愛メッセージ"
    else:
        mood = "夜のあいさつを含み、心を癒す優しい恋愛メッセージ"

    prompt = f"""
    あなたは恋愛AI『カケル』です。
    以下は最近の反応傾向です：
    {feedback}

    {mood}を3〜4文で作成してください。
    傾向を少し意識しつつも、過去の1投稿に偏りすぎない“広がりのある言葉”にしてください。
    絵文字は1つだけ。人を癒す柔らかいトーンで。
    """

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.8,
            max_tokens=200
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        print("❌ GPT生成エラー:", e)
        return None

def save_post(tweet_id, text):
    if not supabase: return
    try:
        supabase.table("posts").insert({
            "tweet_id": tweet_id,
            "text": text,
            "likes": 0, "retweets": 0,
            "created_at": now_iso()
        }).execute()
    except Exception as e:
        print("❌ 投稿保存失敗:", e)

@app.route("/cron/post_tweet")
def post_tweet():
    """朝夜にXへ自動投稿（誘導文つき）"""
    check_key()
    hour = datetime.now(TZ).hour
    if 5 <= hour < 12:
        time_type, icon = "morning", "🌤"
    elif 20 <= hour < 24:
        time_type, icon = "night", "🌙"
    else:
        return jsonify({"status": "skipped"}), 200

    twitter = get_twitter_client()
    if not twitter:
        return "Xクライアント未設定", 500

    quote = generate_ai_post(time_type)
    if not quote:
        return jsonify({"error": "Failed to generate text"}), 500

    invite = random.choice([
        f"💎 AI相談室はこちらから登録お願いします👇\n🔗 {LINE_LINK}",
        f"🩵 カケルAI相談室はこちらからどうぞ👇\n🔗 {LINE_LINK}",
        f"🌙 AI相談室で、心を休めてね👇\n🔗 {LINE_LINK}",
    ])
    text = f"{icon} {quote}\n\n{invite}\n#恋愛AI #カケル #恋愛相談"

    try:
        tweet = twitter.create_tweet(text=text)
        tweet_id = tweet.data["id"]
        save_post(tweet_id, text)
        print("✅ 投稿完了:", text)
        return jsonify({"status": "success", "tweet_id": tweet_id})
    except Exception as e:
        print("❌ 投稿エラー:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/cron/update_stats")
def update_stats():
    """いいね・RT数を取得してSupabaseに更新（3時間おき推奨）"""
    check_key()
    twitter = get_twitter_client()
    if not twitter or not supabase:
        return "設定エラー", 500
    res = supabase.table("posts").select("id, tweet_id").order("created_at", desc=True).limit(10).execute()
    for p in res.data or []:
        try:
            t = twitter.get_tweet(p["tweet_id"], tweet_fields=["public_metrics"])
            m = t.data["public_metrics"]
            supabase.table("posts").update({
                "likes": m["like_count"],
                "retweets": m["retweet_count"]
            }).eq("id", p["id"]).execute()
        except Exception as e:
            print("⚠️ 更新失敗:", e)
    return "✅ Stats updated", 200

# ========================
# Keep-alive
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
# Health / Root
# ========================
@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def home():
    return "🌸 Kakeru Premium Bot running gently with love & memory. + X Auto-Post"

# ========================
# Main
# ========================
if __name__ == "__main__":
    keep_alive()
    app.run(host="0.0.0.0", port=10000)
