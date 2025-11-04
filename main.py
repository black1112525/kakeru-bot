import os
import random
from datetime import datetime, timezone
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage

# ======== 環境変数 ========
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CRON_KEY = os.getenv("CRON_KEY", "secret123")

# ======== Flask初期化 ========
app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ======== Renderヘルスチェック対応 ========
@app.route("/health", methods=["GET", "HEAD"])
def health():
    return "OK", 200


# ======== おみくじロジック ========
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
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
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
        "#カケル占い"
    )
    return msg


# ======== Cron Job用エンドポイント ========
@app.get("/cron/omikuji")
def cron_omikuji():
    if request.args.get("key") != CRON_KEY:
        abort(403)
    msg = build_daily_omikuji()
    line_bot_api.broadcast(messages=[TextSendMessage(text=msg)])
    return "OK", 200


# ======== トップページ ========
@app.route("/")
def index():
    return "✅ Kakeru Omikuji is running!"


# ======== メイン起動 ========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
