try:
    now = datetime.now(TZ)
    start = now - timedelta(days=7)
    res = supabase.table("logs").select("*").gte("created_at", start.isoformat()).execute()
    logs = res.data

    if not logs:
        report = "📊今週のログはありません。"
    else:
        total = len(logs)
        types = {}
        for l in logs:
            t = l.get("type", "unknown")
            types[t] = types.get(t, 0) + 1

        # 改行と日本語を安全に扱うように修正
        report_lines = [f"{k}: {v}件" for k, v in types.items()]
        type_summary = "\\n".join(report_lines)

        report = "📊【カケル週報】\\n"
        report += f"記録件数: {total}件\\n"
        report += f"{type_summary}\\n"

        # AI要約
        analysis_prompt = (
            "以下は今週の会話ログの一部です。主要な相談テーマを3点以内で要約し、"
            "次週に向けた運用改善案を2点、簡潔に提案してください。出力は80〜120字程度で。"
        )

        ai_res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "あなたは恋愛相談AI『カケル』の週報アシスタントです。"},
                {"role": "user", "content": analysis_prompt + "\\n\\n" + json.dumps(logs)[:3000]},
            ],
            temperature=0.6,
            max_tokens=160,
        )
        summary = ai_res.choices[0].message.content.strip()
        report += f"\\n🧠AI分析:\\n{summary}"

    send_line_message(ADMIN_ID, report[:490])
    log_message_to_supabase(ADMIN_ID, report, "weekly_report")
    return "✅ Weekly report sent"
except Exception as e:
    print(f"❌ Weekly report error: {e}")
    return str(e)
