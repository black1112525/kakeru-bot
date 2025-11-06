import os
from datetime import datetime
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, FollowEvent
)
from openai import OpenAI

# ===== 環境変数 =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CRON_KEY = os.getenv("CRON_KEY")

# ===== Flask初期化 =====
app = Flask(__name__)

# ===== LINE初期化 =====
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ===== OpenAI初期化 =====
client = OpenAI(api_key=OPENAI_API_KEY)


# ===== Webhook =====
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# ===== 友達追加メッセージ =====
@handler.add(FollowEvent)
def handle_follow(event):
    welcome = (
        "🌟友だち追加ありがとうございます！🌟\n\n"
        "AIアシスタントのカケルです。\n"
        "話しかけてくれた内容に合わせてお手伝いします！\n\n"
        "・雑談したい\n"
        "・文章を考えてほしい\n"
        "・アイデアを出したい\n\n"
        "なんでも気軽に話しかけてください😊"
    )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=welcome))


# ===== 通常メッセージ =====
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "あなたは優しく誠実な男性AI『カケル』です。"},
                {"role": "user", "content": user_message}
            ]
        )
        reply_text = response.choices[0].message.content.strip()
    except Exception as e:
        reply_text = f"⚠️エラー: {e}"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


# ===== 定期配信 共通関数 =====
def check_key(req_key):
    if req_key != CRON_KEY:
        abort(403)


def send_broadcast(message):
    try:
        line_bot_api.broadcast(TextSendMessage(text=message))
        print("✅ Broadcast sent")
    except Exception as e:
        print(f"⚠️ Broadcast error: {e}")


# ===== 朝おみくじ =====
@app.route("/cron/omikuji")
def cron_omikuji():
    check_key(request.args.get("key"))
    message = (
        "🌞おはよう、今日のカケルのおみくじ🌈\n\n"
        "💖恋愛運：小吉\n返信は短く丁寧に。焦らない。\n\n"
        "💼仕事運：中吉\n整理整頓が運気アップのカギ✨\n\n"
        "🎲ギャンブル運：大吉\nひらめいたタイミングを逃すな🔥\n\n"
        "🎯ラッキーアクション：ありがとうを1回多く言う\n"
        "🎨ラッキーカラー：ボルドー\n\n"
        "焦らず、自分らしくいこう☀️\n#カケル占い"
    )
    send_broadcast(message)
    return "OK"


# ===== 月曜 =====
@app.route("/cron/monday")
def cron_monday():
    check_key(request.args.get("key"))
    msg = (
        "🌅今週のテーマ🌅\n"
        "新しい挑戦を始める週。迷うなら“やってみる”を選ぼう！\n\n"
        "焦らず、自分のペースで行こう💪\n#カケル週間メッセージ"
    )
    send_broadcast(msg)
    return "OK"


# ===== 水曜 =====
@app.route("/cron/wednesday")
def cron_wednesday():
    check_key(request.args.get("key"))
    msg = (
        "🌙水曜ヒント🌙\n"
        "人間関係は“共感”がカギ。聞き役に回ると運気アップ✨\n\n"
        "#カケルヒント"
    )
    send_broadcast(msg)
    return "OK"


# ===== 金曜 =====
@app.route("/cron/friday")
def cron_friday():
    check_key(request.args.get("key"))
    msg = (
        "🌃金曜リラックス🌃\n"
        "今週もおつかれさま！小さなご褒美を自分にあげよう🍀\n\n"
        "#カケル週末メッセージ"
    )
    send_broadcast(msg)
    return "OK"


# ===== 日曜 =====
@app.route("/cron/sunday")
def cron_sunday():
    check_key(request.args.get("key"))
    msg = (
        "🌞日曜リセット🌞\n"
        "心と体を整える時間をとって。次の週に備えてね✨\n\n"
        "#カケル日曜リセット"
    )
    send_broadcast(msg)
    return "OK"


# ===== 満月・新月メッセージ =====
@app.route("/cron/moon_auto")
def cron_moon_auto():
    check_key(request.args.get("key"))
    today = datetime.now(pytz.timezone("Asia/Tokyo")).day
    phase = None
    text = ""

    if today in [1, 15]:
        phase = "🌕満月"
        text = "感謝を伝える日。誰かに“ありがとう”を贈ろう✨"
    elif today in [29, 30]:
        phase = "🌑新月"
        text = "新しい目標を決めるチャンス🌱"

    if phase:
        msg = f"{phase}メッセージ🌙\n{text}\n\n#カケル占い"
        send_broadcast(msg)

    return "OK"


# ===== Renderの死活監視用 =====
@app.route("/health")
def health():
    return "OK", 200


# ===== 動作確認ページ =====
@app.route("/")
def index():
    return "✅ Kakeru Bot is running!"


# ===== 起動 =====
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
