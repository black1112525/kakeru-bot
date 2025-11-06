import os
import random
import math
from datetime import datetime, timedelta, timezone
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
from openai import OpenAI

# ====== 環境変数 ======
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CRON_KEY = os.getenv("CRON_KEY", "yukito")

# ====== 初期化 ======
app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)
JST = timezone(timedelta(hours=9))

# ====== 健康チェック ======
@app.route("/health", methods=["GET", "HEAD"])
def health():
    return "OK", 200

# ====== 友達追加 ======
@handler.add(FollowEvent)
def handle_follow(event):
    msg = (
        "🌟友だち追加ありがとう！🌟\n\n"
        "俺はAIアシスタントのカケル。\n"
        "話しかけてくれた内容に合わせて力になるよ。\n\n"
        "雑談・相談・アイデア出し、なんでもOK。\n"
        "— カケル 🤍"
    )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

# ====== メッセージ応答 ======
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "あなたは優しく丁寧な男性AI『カケル』です。"},
                {"role": "user", "content": user_message},
            ],
        )
        reply_text = response.choices[0].message.content.strip() + "\n\n— カケル 🤍"
    except Exception as e:
        reply_text = f"⚠️エラーが発生しました: {e}"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# ====== おみくじ ======
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
    today = datetime.now(JST).strftime("%Y%m%d")
    rnd = random.Random(int(today))
    ranks = [("大吉",10),("中吉",25),("小吉",25),("吉",25),("末吉",12),("凶",3)]
    love = {
        "大吉":"素直に好意を見せると一歩進む日。",
        "中吉":"挨拶＋目を見て笑顔、効果高め。",
        "小吉":"返信は短く丁寧に。焦らない。",
        "吉":"相手の近況を一つ深掘りしよう。",
        "末吉":"無理に誘わず“次回の伏線”だけ置く。",
        "凶":"既読数に敏感にならないで。深呼吸。"
    }
    work = {
        "大吉":"朝イチ“5分タスク”を3つ片づけると波に乗れる。",
        "中吉":"ToDoを3つに絞ると集中力UP。",
        "小吉":"相談は結論→理由→相談の順で。",
        "吉":"資料はタイトルを一段わかりやすく。",
        "末吉":"夜まで持ち越す案件は早めに“見積り共有”。",
        "凶":"詰まったら15分離れる。戻ると解ける。"
    }
    gamble = {
        "大吉":"“勝ち逃げ”が最適解。上限を決めて。",
        "中吉":"少額で遊ぶと運が活きる日。",
        "小吉":"追わない勇気が吉。",
        "吉":"観戦モードが賢い選択。",
        "末吉":"今日は勉強日。ルール研究が実は大吉。",
        "凶":"無理は禁物。余暇費の範囲で。"
    }
    actions = ["いつもより2分早く家を出る","机を拭く","ありがとうを1回多く言う","10分散歩","コーヒーをブラックで一杯","深呼吸×3"]
    colors = ["ネイビー","オリーブ","ボルドー","サックスブルー","チャコール"]
    pos = ["今日もきっといい日になるよ🌈","無理せず、自分のペースでいこう☀️","笑顔でスタートしよう😊","焦らず、自分らしくいこう🌸","どんな日も前向きに、あなたらしく✨"]
    rank = lambda: _weighted_choice(random.Random(rnd.random()), ranks)
    msg = (
        "☀️おはよう、今日のカケルのおみくじ🌈\n\n"
        f"💖 恋愛運：{rank()}\n{love[rank()]}\n\n"
        f"💼 仕事運：{rank()}\n{work[rank()]}\n\n"
        f"🎲 ギャンブル運：{rank()}\n{gamble[rank()]}\n\n"
        f"🎯 ラッキーアクション：{random.choice(actions)}\n"
        f"🎨 ラッキーカラー：{random.choice(colors)}\n\n"
        f"{random.choice(pos)}\n"
        "— カケル 🤍\n"
        "#カケル占い"
    )
    return msg

@app.get("/cron/omikuji")
def cron_omikuji():
    if request.args.get("key") != CRON_KEY: abort(403)
    line_bot_api.broadcast([TextSendMessage(text=build_daily_omikuji())])
    return "OK", 200

# ====== 週次メッセージ ======
def sign(e): return f"\n— カケル {e}"

def monday_msg():
    l = ["今週は“自分を信じて一歩進む”を合言葉にいこう。",
         "完璧よりも、まずは着手。小さな一歩が流れを作る。",
         "迷ったら、今日の“5分だけ”に集中しよう。"]
    return "☀️おはよう。今週のテーマだ。\n"+random.choice(l)+sign("☀️")

def wednesday_msg():
    l = ["相手の言葉を最後まで聞く“余白”が、関係を優しく強くするよ。",
         "返事に迷ったら、“気持ちは嬉しい”を添えてみよう。",
         "自分を責めないで。うまくいかない日ほど、優しさを自分に。"]
    return "💬今週の恋と心のヒント。\n"+random.choice(l)+sign("💬")

def friday_msg():
    l = ["一週間おつかれさま。頑張った自分に“よくやった”をあげよう。",
         "今日は早めに切り上げて、心の空気を入れ替えよう。",
         "小さな達成を数えて、静かな夜を。"]
    return "🌙一週間おつかれ。\n"+random.choice(l)+sign("🌙")

def sunday_msg():
    l = ["深呼吸して、荷物を一つ置こう。明日が軽くなる。",
         "今日のうちに“やらないこと”を決めると、月曜が優しくなる。",
         "心の中を片づけて、静かな準備を。"]
    return "✨リセットと準備の一言。\n"+random.choice(l)+sign("✨")

# ====== 満月・新月（ランダム多パターン） ======
def full_moon_msg():
    sets = [
        ["満ちた分だけ、手放せる。もう抱えなくていい想いを一つ、そっと置こう。",
         "“もう大丈夫”と言えるものを選んで、感謝と一緒に手放す夜。",
         "余白ができると、新しい光が入ってくる。"],
        ["感情の波が静まるとき、優しさが残る。今日は“許す”を選ぼう。",
         "自分を責めずに、ただ受け入れる。それが浄化の一歩。"]
    ]
    return "🌕今夜は満月。\n"+random.choice(random.choice(sets))+sign("🌕")

def new_moon_msg():
    sets = [
        ["心を白紙にして、叶えたいことを一つ決めよう。小さくていい、今日から始めよう。",
         "新しい流れに乗る準備。最初の一歩は“宣言”から。",
         "静かな願いほど、長く強く続いていく。"],
        ["未来の種を蒔く夜。今の想いを言葉にして、宇宙に預けよう。",
         "“こうなりたい”を1行メモして眠るだけで、流れは変わる。"]
    ]
    return "🌑今夜は新月。\n"+random.choice(random.choice(sets))+sign("🌑")

# ====== 自動月判定 ======
def get_moon_phase(date: datetime):
    diff = date - datetime(2001, 1, 1, tzinfo=JST)
    days = diff.days + (diff.seconds / 86400)
    lunations = 0.20439731 + (days * 0.03386319269)
    phase_index = lunations % 1
    if phase_index < 0.03 or phase_index > 0.97: return "new"
    elif 0.47 < phase_index < 0.53: return "full"
    return None

@app.get("/cron/moon_auto")
def cron_moon_auto():
    if request.args.get("key") != CRON_KEY: abort(403)
    today = datetime.now(JST)
    phase = get_moon_phase(today)
    if phase == "full":
        msg, tag = full_moon_msg(), "満月"
    elif phase == "new":
        msg, tag = new_moon_msg(), "新月"
    else:
        return f"🌗 Not full/new moon ({today.strftime('%Y-%m-%d')})", 200
    line_bot_api.broadcast([TextSendMessage(text=msg)])
    return f"🌕 Sent {tag} message!", 200

# ====== Webhook ======
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@app.route("/")
def index():
    return "✅ Kakeru Auto Omikuji + Weekly + MoonAuto is running!"

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
# ===== 定期配信用ルート =====
from datetime import datetime
import pytz

@app.route("/cron/monday")
def cron_monday():
    key = request.args.get("key")
    if key != os.getenv("CRON_KEY"):
        return "Forbidden", 403

    message = (
        "🌅今週のテーマ🌅\n"
        "新しい一歩を踏み出す週。迷うなら“やってみる”を選んでみよう！\n\n"
        "焦らず、自分のペースで行こう💪\n"
        "#カケル週間メッセージ"
    )

    send_broadcast(message)
    return "OK"

@app.route("/cron/wednesday")
def cron_wednesday():
    key = request.args.get("key")
    if key != os.getenv("CRON_KEY"):
        return "Forbidden", 403

    message = (
        "🌙水曜ヒント🌙\n"
        "人間関係は“共感”がカギ。聞き役に回ると運気アップ✨\n\n"
        "#カケルヒント"
    )

    send_broadcast(message)
    return "OK"

@app.route("/cron/friday")
def cron_friday():
    key = request.args.get("key")
    if key != os.getenv("CRON_KEY"):
        return "Forbidden", 403

    message = (
        "🌃金曜リラックス🌃\n"
        "今週もおつかれさま！小さなご褒美を自分にあげよう🍀\n\n"
        "#カケル週末メッセージ"
    )

    send_broadcast(message)
    return "OK"

@app.route("/cron/sunday")
def cron_sunday():
    key = request.args.get("key")
    if key != os.getenv("CRON_KEY"):
        return "Forbidden", 403

    message = (
        "🌞日曜リセット🌞\n"
        "心と体を整える時間をとって。次の週に備えてね✨\n\n"
        "#カケル日曜リセット"
    )

    send_broadcast(message)
    return "OK"

@app.route("/cron/moon_auto")
def cron_moon_auto():
    key = request.args.get("key")
    if key != os.getenv("CRON_KEY"):
        return "Forbidden", 403

    # シンプルに満月／新月っぽいメッセージ
    today = datetime.now(pytz.timezone("Asia/Tokyo")).day
    if today in [1, 15]:
        phase = "🌕満月"
        msg = "感謝を伝える日。誰かに“ありがとう”を贈ろう✨"
    elif today in [29, 30]:
        phase = "🌑新月"
        msg = "新しい目標を決めるチャンス🌱"
    else:
        return "OK (no moon event today)"

    message = f"{phase}メッセージ🌙\n{msg}\n\n#カケル占い"
    send_broadcast(message)
    return "OK"


# ===== LINEへの一斉送信用 =====
def send_broadcast(message):
    try:
        line_bot_api.broadcast(TextSendMessage(text=message))
    except Exception as e:
        print(f"LINE送信エラー: {e}")
