import os
import httpx
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    FollowEvent
)
from openai import OpenAI

# ===== 環境変数 =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# ===== Flaskアプリ初期化 =====
app = Flask(__name__)

# ===== LINE Bot初期化 =====
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ===== OpenAI初期化（httpx使用） =====
http_client = httpx.Client(timeout=30.0)
client = OpenAI(api_key=OPENAI_API_KEY, http_client=http_client)


# ===== LINE Webhook =====
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# ===== 友達追加時の自動メッセージ =====
@handler.add(FollowEvent)
def handle_follow(event):
    welcome_text = (
        "🌟友だち追加ありがとうございます！🌟\n\n"
        "私はAIアシスタントのカケルです。\n"
        "話しかけてくれた内容に合わせてお手伝いします！\n\n"
        "例えば：\n"
        "・雑談したい\n"
        "・文章を考えてほしい\n"
        "・アイデアを出したい\n\n"
        "なんでも気軽に聞いてくださいね😊"
    )

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=welcome_text)
    )


# ===== ユーザーのメッセージを処理 =====
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "あなたは親切で丁寧なアシスタントです。"},
                {"role": "user", "content": user_message},
            ],
        )
        reply_text = response.choices[0].message.content.strip()

    except Exception as e:
        reply_text = f"⚠️エラーが発生しました: {e}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


# ===== 動作確認用のトップページ =====
@app.route("/")
def index():
    return "✅ Kakeru Bot is running!"


# ✅ Renderヘルスチェック対応（これが重要！）
@app.route("/health", methods=["GET", "HEAD"])
def health():
    return "OK", 200


# ===== メイン起動 =====
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
