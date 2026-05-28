import os
import json
import time
import threading
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from flask_mail import Mail, Message

app = Flask(__name__)
app.config['MAIL_SERVER']         = 'smtp.gmail.com'
app.config['MAIL_PORT']           = 587
app.config['MAIL_USE_TLS']        = True
app.config['MAIL_USERNAME']       = os.environ.get('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD']       = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME', '')
mail = Mail(app)

# 儲存查詢狀態（記憶體內，重啟清空）
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

def get_gemini_url():
    key = os.environ.get('GEMINI_API_KEY', '')
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash-lite:generateContent?key={key}"
    )

def ask_gemini(prompt, retry=4):
    url = get_gemini_url()
    for attempt in range(retry):
        try:
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=180
            )
            if resp.status_code == 429:
                time.sleep(30 * (attempt + 1))
                continue
            if resp.status_code == 403:
                raise Exception("Gemini API Key 無效 (403)")
            if resp.status_code == 404:
                raise Exception("Gemini 模型名稱錯誤 (404)")
            resp.raise_for_status()
            candidates = resp.json().get("candidates", [])
            if not candidates:
                raise Exception("Gemini 回應無內容")
            return candidates[0]["content"]["parts"][0]["text"]
        except requests.exceptions.Timeout:
            if attempt == retry - 1:
                raise Exception("Gemini API 回應逾時")
            time.sleep(10)
    raise Exception("Gemini API 速率限制，請稍後再試")

def build_reports():
    ranges = get_time_ranges()
    today_str = datetime.today().strftime("%Y年%m月%d日")

    prompt = f"""今天是 {today_str}。整合近3個月台灣 PTT/Dcard/Threads 信貸核貸心得。
輸出純 JSON（不含 markdown、不含說明文字），結構如下：

{{
  "summary1m": "近一個月市場摘要25字內",
  "summary3m": "近三個月市場摘要25字內",
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

    raw = ask_gemini(prompt)
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
        raise Exception("Gemini 未回傳銀行資料，請重試")

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

def run_job(job_id, email):
    """背景執行查詢，完成後寄信"""
    jobs[job_id]["status"] = "running"
    jobs[job_id]["message"] = "正在查詢 Gemini AI…"

    try:
        reports = build_reports()
        jobs[job_id]["reports"] = reports
        jobs[job_id]["message"] = "查詢完成"

        # 寄信
        if email and "@" in email:
            jobs[job_id]["message"] = "寄送報告中…"
            with app.app_context():
                html_body = render_template(
                    "email.html", reports=reports,
                    generated_at=datetime.today().strftime("%Y-%m-%d %H:%M")
                )
                mail.send(Message(
                    subject=f"信貸統計表｜{datetime.today().strftime('%Y-%m-%d')}",
                    recipients=[email],
                    html=html_body
                ))
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
    """立刻回應 job_id，背景執行查詢"""
    data  = request.json or {}
    email = data.get("email", "").strip()

    job_id = datetime.today().strftime("%Y%m%d%H%M%S")
    jobs[job_id] = {
        "status": "queued",
        "message": "查詢已排隊",
        "reports": None,
        "mailSent": False,
        "email": email,
    }

    t = threading.Thread(target=run_job, args=(job_id, email), daemon=True)
    t.start()

    return jsonify({"success": True, "jobId": job_id})

@app.route("/api/status/<job_id>")
def status(job_id):
    """前端輪詢這個 endpoint 取得進度"""
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
        "gemini_key_set": bool(os.environ.get('GEMINI_API_KEY', '')),
        "mail_user_set":  bool(os.environ.get('MAIL_USERNAME', '')),
        "mail_pass_set":  bool(os.environ.get('MAIL_PASSWORD', '')),
    })

if __name__ == "__main__":
    app.run(debug=True)
