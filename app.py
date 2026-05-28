import os
import json
import time
import threading
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ── Groq API ──
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── Resend API ──
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
RESEND_URL = "https://api.resend.com/emails"
MAIL_FROM = os.environ.get('MAIL_FROM', 'onboarding@resend.dev')

# ── 查詢狀態儲存 ──
jobs = {}

BANKS_LIST = (
    "土地銀行、合作金庫、第一銀行、華南銀行、彰化銀行、兆豐銀行、"
    "凱基銀行、國泰世華、中國信託、台北富邦、星展銀行、渣打銀行、"
    "滙豐銀行、玉山銀行、台新銀行、遠東商銀、台中銀行、永豐銀行、"
    "連線銀行、將來銀行、樂天銀行、安泰銀行、遠東銀行"
)

def get_time_ranges():
    today = datetime.today()
    fmt = "%Y年%m月%d日"
    return {
        "1m": f"{(today-timedelta(days=30)).strftime(fmt)} ~ {today.strftime(fmt)}",
        "3m": f"{(today-timedelta(days=90)).strftime(fmt)} ~ {today.strftime(fmt)}",
    }

def ask_groq(prompt, retry=3):
    for attempt in range(retry):
        try:
            resp = requests.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 4000,
                },
                timeout=120
            )
            if resp.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout:
            if attempt == retry - 1:
                raise Exception("Groq API 回應逾時，請重試")
            time.sleep(5)
    raise Exception("Groq API 速率限制，請稍後再試")

def build_reports():
    ranges = get_time_ranges()
    today_str = datetime.today().strftime("%Y年%m月%d日")

    prompt = f"""你是台灣信貸社群輿情分析師。今天是 {today_str}。
整合近3個月台灣 PTT（Loan/Bank_Service板）、Dcard（理財板）、Threads 的信貸核貸心得。

輸出純 JSON（不含 markdown、不含說明文字），結構如下：

{{
  "summary1m": "近一個月（{ranges['1m']}）市場摘要25字內",
  "summary3m": "近三個月（{ranges['3m']}）市場摘要25字內",
  "banks": [
    {{
      "name": "土地銀行",
      "tier": 1,
      "rateRange": "2.16%~2.45%",
      "conditions": "條件說明",
      "spec": "核心規格20字內",
      "community": "社群回報（來源：PTT/Dcard）",
      "recentChange": "近一個月變化，無則填無",
      "lowSample": false
    }}
  ],
  "buzz": [
    {{"rank":1,"icon":"👑","bank":"銀行名","reason":"熱議原因30字內","target":"適合誰"}}
  ]
}}

banks 必須包含全部23家：{BANKS_LIST}
tier規則：1=APR<2.4%，2=APR 2.5~2.8%，3=APR>2.8%
buzz列前4名
只輸出JSON，第一個字元是 {{"""

    raw = ask_groq(prompt)
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e == -1:
            raise ValueError("找不到 JSON")
        data = json.loads(raw[s:e+1])
    except Exception as ex:
        raise Exception(f"JSON解析失敗: {ex} | 原文前200字: {raw[:200]}")

    all_banks  = data.get("banks", [])
    buzz       = data.get("buzz", [])
    summary_1m = data.get("summary1m", "近一個月台灣信貸市場行情")
    summary_3m = data.get("summary3m", "近三個月台灣信貸市場行情")

    if not all_banks:
        raise Exception("未取得銀行資料，請重試")

    def sort_key(b):
        try:
            rate = float(b.get("rateRange","9%").split("~")[0].replace("%","").strip())
        except:
            rate = 9.0
        return (b.get("tier", 2), rate)

    all_banks.sort(key=sort_key)
    for i, b in enumerate(all_banks):
        b["rank"] = i + 1

    banks_1m = []
    for b in all_banks:
        b1 = dict(b)
        change = b.get("recentChange", "無")
        if change and change not in ("無", "無明顯變化", "無變化", ""):
            b1["community"] = f"【近期】{change}｜{b.get('community','')}"
        banks_1m.append(b1)

    return [
        {"label":"近一個月","timeRange":ranges["1m"],"summary":summary_1m,"banks":banks_1m,"buzz":buzz},
        {"label":"近三個月","timeRange":ranges["3m"],"summary":summary_3m,"banks":all_banks,"buzz":buzz},
    ]

def send_resend(to_email, reports):
    """用 Resend HTTP API 寄信，不需要 SMTP"""
    html_body = render_template(
        "email.html",
        reports=reports,
        generated_at=datetime.today().strftime("%Y-%m-%d %H:%M")
    )
    resp = requests.post(
        RESEND_URL,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "from": MAIL_FROM,
            "to": [to_email],
            "subject": f"信貸統計表｜近1個月 & 近3個月｜{datetime.today().strftime('%Y-%m-%d')}",
            "html": html_body
        },
        timeout=30
    )
    if resp.status_code not in (200, 201):
        raise Exception(f"Resend 寄信失敗: {resp.text}")
    return True

def run_job(job_id, email):
    jobs[job_id]["status"] = "running"
    jobs[job_id]["message"] = "AI 分析社群輿情中…"
    try:
        reports = build_reports()
        jobs[job_id]["reports"] = reports
        jobs[job_id]["message"] = "查詢完成，寄送報告中…"

        if email and "@" in email:
            with app.app_context():
                send_resend(email, reports)
            jobs[job_id]["mailSent"] = True

        jobs[job_id]["status"] = "done"
        jobs[job_id]["message"] = "完成！"

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["message"] = str(e)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/query", methods=["POST"])
def query():
    data  = request.json or {}
    email = data.get("email", "").strip()

    job_id = datetime.today().strftime("%Y%m%d%H%M%S%f")
    jobs[job_id] = {
        "status": "queued",
        "message": "查詢已排隊",
        "reports": None,
        "mailSent": False,
    }
    threading.Thread(target=run_job, args=(job_id, email), daemon=True).start()
    return jsonify({"success": True, "jobId": job_id})

@app.route("/api/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify({
        "status":   job["status"],
        "message":  job["message"],
        "reports":  job["reports"],
        "mailSent": job["mailSent"],
    })

@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "groq_key_set":   bool(GROQ_API_KEY),
        "resend_key_set": bool(RESEND_API_KEY),
    })

if __name__ == "__main__":
    app.run(debug=True)
