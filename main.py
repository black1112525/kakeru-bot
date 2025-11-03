import os
import sys
import time
import json
import random
import logging
import psycopg2
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from flask import Flask, request, abort

# ====== LINE SDK ======
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# ====== Flask ======
app = Flask(__name__)

# ====== ENV ======
DATABASE_URL = os.getenv("DATABASE_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # 必須（GPT使用）
CRON_TOKEN = os.getenv("CRON_TOKEN")          # 任意：Cron保護用

RENDER_PORT = int(os.getenv("PORT", 5000))

REQUIRED_ENVS = [DATABASE_URL, LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET]
if not all(REQUIRED_ENVS):
    print("❌ 必須の環境変数が不足しています。Renderの環境変数を確認してください。")
    sys.exit(1)

# ====== LINE設定 ======
handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# ====== ログ ======
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kakeru")

# ====== レート制限（秒） ======
last_hit = defaultdict(lambda: 0)
def rate_limited(uid: str, interval: int = 3) -> bool:
    now = time.time()
    if now - last_hit[uid] < interval:
        return True
    last_hit[uid] = now
    return False

# ====== DBユーティリティ ======
def db():
    return psycopg2.connect(DATABASE_URL)

def init_tables():
    conn = db()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_data (
                user_id TEXT PRIMARY KEY,
                talk_count INTEGER DEFAULT 0,
                last_talk TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                user_id TEXT,
                message TEXT,
                timestamp TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_summary (
                user_id TEXT PRIMARY KEY,
                summary TEXT,
                updated_at TIMESTAMP
            );
        """)
    conn.commit()
    conn.close()

init_tables()

# ====== 安全返信 ======
def safe_reply(token: str, text: str):
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=token,
                    messages=[TextMessage(text=text)]
                )
            )
    except Exception as e:
        logger.warning(f"[LINE送信エラー] {e}")

# ====== OpenAIユーティリティ ======
OPENAI_TIMEOUT = 12  # sec
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MOD_URL  = "https://api.openai.com/v1/moderations"
OPENAI_MODEL = "gpt-4o-mini"  # コスパ◎

def openai_chat(messages, temperature=0.8, max_tokens=320):
    if not OPENAI_API_KEY:
        return None
    try:
        res = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=OPENAI_TIMEOUT,
        )
        if res.status_code != 200:
            logger.warning(f"[OpenAI API 非200] {res.status_code} {res.text[:300]}")
            return None
        data = res.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"[OpenAI API 例外] {e}")
        return None

def openai_moderation(text: str) -> bool:
    """危険判定: Trueならブロック"""
    if not OPENAI_API_KEY:
        return False
    try:
        r = requests.post(
            OPENAI_MOD_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": "omni-moderation-latest", "input": text},
            timeout=8,
        )
        if r.status_code != 200:
            return False
        return r.json()["results"][0]["flagged"]
    except Exception:
        return False

# ====== 会話分類（GPTを使うか判定） ======
CONSULT_KEYWORDS = [
    "告白", "失恋", "復縁", "別れ", "既読", "未読", "脈", "気になる",
    "距離", "返信", "デート", "彼女", "彼氏", "恋愛", "好き", "不安", "冷たい",
]
def should_use_gpt(text: str) -> bool:
    if len(text) >= 20:
        return True
    if any(k in text for k in CONSULT_KEYWORDS):
        return True
    return False

# ====== 要点要約（直近の相談から短い要約を作る） ======
def build_summary_snippet(messages: list[str]) -> str:
    """直近の複数メッセージから、ごく短い要約を作成"""
    if not OPENAI_API_KEY or not messages:
        return ""
    sys_prompt = (
        "あなたは短い要約を作るアシスタントです。"
        "日本語で一行～二行、名詞中心で簡潔に、個人情報や固有名は省略して要点だけ書いてください。"
    )
    user_prompt = "直近相談の要点を短く要約:\n- " + "\n- ".join(messages[-3:])
    out = openai_chat(
        [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
        temperature=0.3,
        max_tokens=80,
    )
    return out or ""

def get_user_summary(user_id: str) -> str:
    conn = db()
    with conn.cursor() as cur:
        cur.execute("SELECT summary FROM user_summary WHERE user_id=%s;", (user_id,))
        row = cur.fetchone()
    conn.close()
    return row[0] if row else ""

def upsert_user_summary(user_id: str, new_summary: str):
    if not new_summary:
        return
    conn = db()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO user_summary (user_id, summary, updated_at)
            VALUES (%s,%s,%s)
            ON CONFLICT (user_id) DO UPDATE SET
                summary=EXCLUDED.summary,
                updated_at=EXCLUDED.updated_at;
        """, (user_id, new_summary, datetime.now(timezone.utc)))
    conn.commit()
    conn.close()

# ====== 履歴保存/取得 ======
def record_and_prune_history(user_id: str, text: str):
    conn = db()
    with conn.cursor() as cur:
        # user_data
        cur.execute("""
            INSERT INTO user_data (user_id, talk_count, last_talk)
            VALUES (%s, 1, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                talk_count = user_data.talk_count + 1,
                last_talk = EXCLUDED.last_talk;
        """, (user_id, datetime.now(timezone.utc)))

        # history 追記
        cur.execute(
            "INSERT INTO chat_history (user_id, message, timestamp) VALUES (%s,%s,%s);",
            (user_id, text, datetime.now(timezone.utc))
        )
        # 最新5件だけ残す（保存しすぎ防止）
        cur.execute("""
            DELETE FROM chat_history
            WHERE user_id=%s AND timestamp NOT IN (
                SELECT timestamp FROM chat_history
                WHERE user_id=%s ORDER BY timestamp DESC LIMIT 5
            );
        """, (user_id, user_id))
    conn.commit()
    conn.close()

def get_talk_count(user_id: str) -> int:
    conn = db()
    with conn.cursor() as cur:
        cur.execute("SELECT talk_count FROM user_data WHERE user_id=%s;", (user_id,))
        row = cur.fetchone()
    conn.close()
    return row[0] if row else 0

def get_recent_texts(user_id: str, limit: int = 5) -> list[str]:
    conn = db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT message FROM chat_history
            WHERE user_id=%s
            ORDER BY timestamp DESC
            LIMIT %s;
        """, (user_id, limit))
        rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows][::-1]  # 古い→新しい順に

# ====== おみくじ ======
def get_love_fortune() -> str:
    fortunes = [
        "💘 大吉：運命の出会いが訪れるかも！積極的に行動してみよう！",
        "💖 中吉：笑顔が恋を引き寄せる日。素直な気持ちを伝えてみて！",
        "💞 小吉：焦らず一歩ずつ。相手のペースを大切にしてね。",
        "💔 凶：今日は自分を癒す日。無理せずリラックスしよう。",
        "💗 吉：連絡するなら夜がチャンス！自然体が一番魅力的。",
    ]
    return random.choice(fortunes)

# ====== Webhook ======
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# ====== メイン応答 ======
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = (event.message.text or "").strip()

    # レート制限 & 入力検証
    if rate_limited(user_id):
        return
    if not text or len(text) > 800:
        safe_reply(event.reply_token, "メッセージは1文字以上800文字以内で送ってね！")
        return

    # モデレーション（安全フィルタ）
    if OPENAI_API_KEY and openai_moderation(text):
        safe_reply(event.reply_token, "ごめんなさい。その話題には答えられません。別の話題で相談してくださいね。")
        return

    # 履歴に保存（直近5件保持）
    record_and_prune_history(user_id, text)
    talk_count = get_talk_count(user_id)

    # 会話レベル（親しみ度）
    if talk_count <= 3:
        level = 1
    elif talk_count <= 10:
        level = 2
    else:
        level = 3

    # 時間帯の挨拶（日本時間）
    hour = datetime.now(timezone(timedelta(hours=9))).hour
    if hour < 10:
        greet = "おはようございます☀️"
    elif hour < 18:
        greet = "こんにちは🌸"
    else:
        greet = "こんばんは🌙"

    # コマンド
    if text in ("履歴リセット", "/clear"):
        # 履歴と要約を消す
        conn = db()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_history WHERE user_id=%s;", (user_id,))
            cur.execute("DELETE FROM user_summary WHERE user_id=%s;", (user_id,))
            cur.execute("UPDATE user_data SET talk_count=0 WHERE user_id=%s;", (user_id,))
        conn.commit()
        conn.close()
        safe_reply(event.reply_token, "会話の記憶をリセットしました。改めて、どんなことでも相談してくださいね。")
        return

    # おみくじ
    if ("おみくじ" in text) or ("占い" in text):
        safe_reply(event.reply_token, f"{greet}\n今日の恋愛運は…\n\n{get_love_fortune()}")
        return

    # ====== ハイブリッド応答判定 ======
    use_gpt = OPENAI_API_KEY is not None and should_use_gpt(text)

    # 既存要約の取得
    summary = get_user_summary(user_id)

    if use_gpt:
        # --- GPT人格＆コンテキスト ---
        sys_prompt = (
            "あなたは男性向けの恋愛カウンセラーAI『カケル』です。"
            "最初は丁寧で落ち着いた口調で、相手を否定せず共感を示し、"
            "必要に応じて穏やかな提案や具体的な一歩を提示してください。"
            "長すぎず（最大400字程度）、安全・誠実に。"
        )
        context = ""
        if summary:
            context = f"■これまでの要約: {summary}\n"
        recent = get_recent_texts(user_id, limit=4)
        if recent:
            context += "■直近のメッセージ:\n- " + "\n- ".join(recent)

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"{context}\n\n■今回の相談:\n{text}"},
        ]
        reply = openai_chat(messages, temperature=0.8, max_tokens=360)

        # フォールバック
        if not reply:
            reply = (
                f"{greet}\n話してくれてありがとうございます。"
                "少し通信が不安定みたいですが、よければ状況をもう一度だけ詳しく教えてください。"
            )

        safe_reply(event.reply_token, reply)

        # --- 要約を更新（直近の相談群から） ---
        recent_for_summary = get_recent_texts(user_id, limit=5)
        new_summary = build_summary_snippet(recent_for_summary)
        if new_summary:
            upsert_user_summary(user_id, new_summary)

    else:
        # --- 固定文（高速応答） ---
        if level == 1:
            if text in ["こんにちは", "おはよう", "こんばんは", "はじめまして"]:
                reply = (
                    f"{greet}\nメッセージありがとうございます。"
                    "恋愛や人間関係で気になることがあれば、遠慮せず話してみてくださいね。"
                )
            else:
                reply = "お話ありがとうございます。よければ、もう少し詳しく教えてもらえますか？"
        elif level == 2:
            if ("疲れ" in text) or ("しんど" in text) or ("つら" in text):
                reply = "無理しすぎていませんか？たまには自分を甘やかしても大丈夫ですよ。"
            elif ("好き" in text) or ("恋" in text):
                reply = "その気持ち、大切にしたいですね。どんな相手なのか、もう少し教えてもらえますか？"
            elif "ありがとう" in text:
                reply = "こちらこそ、話してくれてありがとうございます。力になれていたら嬉しいです。"
            else:
                reply = "なるほど…少し気持ちが整理できてきたかもしれませんね。続きも聞かせてください。"
        else:
            if ("疲れ" in text):
                reply = "おつかれさま。今日もよく頑張りましたね。自分に小さなご褒美、どうでしょう？"
            elif ("好き" in text):
                reply = "いいね、その気持ち。どんな人で、どの辺りが好きだと思った？"
            elif ("別れ" in text) or ("失恋" in text):
                reply = "つらかったですね…。無理せず、あなたのペースで話していきましょう。俺はここにいます。"
            else:
                reply = "うんうん、なるほど。それで、あなたはどうしていきたいと思っていますか？"

        # 既存要約があれば、そっと思い出すひと言を添える（親しみ増加時）
        if level >= 2 and summary:
            reply = f"{reply}\n\n（前にお話ししていた件も、少しずつ動かしていけると良いですね）"

        safe_reply(event.reply_token, reply)

# ====== ルート/ヘルス ======
@app.route("/")
def home():
    return "KakeruBot is running 🚀"

# ====== Cron配信用（毎朝の占い） ======
@app.route("/cron/daily-uraniai", methods=["POST"])
def cron_daily():
    if CRON_TOKEN and request.headers.get("X-Cron-Token") != CRON_TOKEN:
        abort(401)

    # ユーザー一覧
    conn = db()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT user_id FROM user_data;")
        users = cur.fetchall()
    conn.close()

    if not users:
        return "No users"

    fortune = get_love_fortune()
    push_message = f"🌅おはようございます！\n今日の恋愛運は…\n\n{fortune}"

    for user in users:
        user_id = user[0]
        try:
            r = requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers={
                    "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"to": user_id, "messages": [{"type": "text", "text": push_message}]},
                timeout=8,
            )
            # ブロック等で 400 の場合は登録を整理（任意）
            if r.status_code == 400:
                conn = db()
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM user_data WHERE user_id=%s;", (user_id,))
                    cur.execute("DELETE FROM chat_history WHERE user_id=%s;", (user_id,))
                    cur.execute("DELETE FROM user_summary WHERE user_id=%s;", (user_id,))
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"[Cron送信エラー] {e}")

    return "OK"

# ====== 起動 ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=RENDER_PORT)

# === 手動でDBリセットしたい時に使う ===
@app.route("/reset-db")
def reset_db():
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS user_data;")
    cur.execute("""
        CREATE TABLE user_data (
            user_id TEXT PRIMARY KEY,
            talk_count INTEGER DEFAULT 0,
            last_talk TIMESTAMP,
            history TEXT,
            last_updated TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    return "✅ データベースをリセットしました！"
