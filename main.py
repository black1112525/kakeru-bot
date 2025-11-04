import os
import random
from datetime import datetime, timedelta, timezone
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
from openai import OpenAI

# ======== 環境変数 ========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CRON_KEY = os.getenv("CRON_KEY", "secret123")

# ======== 初期化 ========
app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

# ======== 会話履歴保持用 ========
user_sessions = {}
MAX_TURNS = 10  # 10往復（20メッセージ）
JST = timezone(timedelta(hours=9))  # 日本時間固定

# ======== ヘルスチェック ========
@app.route("/health", methods=["GET", "HEAD"])
def health():
    return "OK", 200


# ======== 友達追加時 ========
@handler.add(FollowEvent)
def handle_follow(event):
    welcome_text = (
        "🌟友だち追加ありがとう！🌟\n\n"
        "俺はAIアシスタントのカケルだよ。\n"
        "話しかけてくれた内容に合わせてお手伝いする！\n\n"
        "雑談・相談・アイデア出し、なんでもOK😊\n\n"
        "— カケル 🤍"
    )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=welcome_text))


# ======== 会話記憶つき（軽量）チャット ========
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text

    # 履歴取得
    history = user_sessions.get(user_id, [])
    history.append({"role": "user", "content": user_message})

    # 要約処理（古い履歴を軽くまとめる）
    if len(history) > MAX_TURNS * 2:
        summary_prompt = "以下の会話を短く要約して、今後の文脈を保てるようにまとめてください。"
        summary_text = "\n".join(
            [f"{m['role']}: {m['content']}" for m in history[-MAX_TURNS * 2 :]]
        )
        try:
            summary_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "あなたは要約アシスタントです。"},
                    {"role": "user", "content": summary_prompt + "\n" + summary_text},
                ],
            )
            summary = summary_response.choices[0].message.content.strip()
            history = [{"role": "system", "content": f"これまでの会話の要約: {summary}"}]
        except Exception:
            history = history[-MAX_TURNS * 2 :]

    try:
        messages = [{"role": "system", "content": "あなたは優しく丁寧な男性AI『カケル』です。"}] + history
        response = client.chat.completions.create(model="gpt-4o-mini", messages=messages)
        reply_text = response.choices[0].message.content.strip()
        reply_text += "\n\n— カケル 🤍"  # ← 署名を追加！

    except Exception as e:
        reply_text = f"⚠️エラーが発生しました: {e}\n— カケル 🤍"

    # 履歴更新
    history.append({"role": "assistant", "content": reply_text})
    user_sessions[user_id] = history

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


# ======== おみくじ ========
def _weighted_choice(rnd, items):
    total = sum(w for _, w in items)
    pick = rnd.uniform(0, total)
    cur = 0
    for v, w in items:
        cur += w
        if pick <= cur:
            return v
    return items[-1][0]


def build_daily_omikuji():
    today = datetime.now(JST).strftime("%Y%m%d")  # JST化済み
    rnd = random.Random(int(today))

    ranks = [("大吉", 10), ("中吉", 25), ("小吉", 25), ("吉", 25), ("末吉", 12), ("凶", 3)]

    love_msgs = {
        "大吉": "素直に好意を見せると一歩進む日。",
        "中吉": "挨拶＋目を見て笑顔、効果高め。",
        "小吉": "返信は短く丁寧に。焦らない。",
        "吉": "相手の近況を一つ深掘りしてみよう。",
        "末吉": "無理に誘わず“次回の伏線”だけ置く。",
        "凶": "既読数に敏感にならないで。深呼吸。"
    }

    work_msgs = {
        "大吉": "朝イチ“5分タスク”を3つ片づけると波に乗れる。",
        "中吉": "ToDoを3つに絞ると集中力UP。",
        "小吉": "相談は結論→理由→相談の順で。",
        "吉": "資料はタイトルを一段わかりやすく。",
        "末吉": "夜まで持ち越す案件は早めに“見積り共有”。",
        "凶": "詰まったら15分離れる。戻ると解ける。"
    }

    gamble_msgs = {
        "大吉": "“勝ち逃げ”が最適解。上限を決めて。",
        "中吉": "少額で遊ぶと運が活きる日。",
        "小吉": "追わない勇気が吉。",
        "吉": "観戦モードが賢い選択。",
        "末吉": "今日は勉強日。ルール研究が実は大吉。",
        "凶": "無理は禁物。余暇費の範囲で。"
    }

    actions = [
        "いつもより2分早く家を出る", "机を拭く", "ありがとうを1回多く言う",
        "10分散歩", "コーヒーはブラックで一杯", "深呼吸×3"
    ]
    colors = ["ネイビー", "オリーブ", "ボルドー", "サックスブルー", "チャコール"]

    positive_msgs = [
        "今日もきっといい日になるよ🌈",
        "無理せず、自分のペースでいこう☀️",
        "笑顔でスタートしよう😊",
        "焦らず、自分らしくいこう🌸",
        "どんな日も前向きに、あなたらしく✨"
    ]

    love_rank = _weighted_choice(rnd, ranks)
    work_rank = _weighted_choice(rnd, ranks)
    gamb_rank = _weighted_choice(rnd, ranks)

    msg = (
        "🌅おはよう、今日のカケルのおみくじ🌈\n\n"
        f"💖 恋愛運：{love_rank}\n{love_msgs[love_rank]}\n\n"
        f"💼 仕事運：{work_rank}\n{work_msgs[work_rank]}\n\n"
        f"🎲 ギャンブル運：{gamb_rank}\n{gamble_msgs[gamb_rank]}\n\n"
        f"🎯 ラッキーアクション：{rnd.choice(actions)}\n"
        f"🎨 ラッキーカラー：{rnd.choice(colors)}\n\n"
        f"{rnd.choice(positive_msgs)}\n"
        "— カケル 🤍\n"
        "#カケル占い"
    )
    return msg


# ======== Cron Job ========
@app.get("/cron/omikuji")
def cron_omikuji():
    if request.args.get("key") != CRON_KEY:
        abort(403)
    msg = build_daily_omikuji()
    line_bot_api.broadcast(messages=[TextSendMessage(text=msg)])
    return "OK", 200


# ======== Webhook ========
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# ======== トップページ ========
@app.route("/")
def index():
    return "✅ Kakeru Chat + Omikuji (JST + 署名入り) is running!"


# ======== 起動 ========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
