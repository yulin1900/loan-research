import os
import json
import time
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from flask_mail import Mail, Message

app = Flask(__name__)

app.config['MAIL_SERVER']         = 'smtp.gmail.com'
app.config['MAIL_PORT']           = 587
app.config['MAIL_USE_TLS']        = True
app.config['MAIL_USERNAME']       = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD']       = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')
mail = Mail(app)

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent?key=" + GEMINI_API_KEY
)

BANKS = [
    "土地銀行","合作金庫","第一銀行","華南銀行","彰化銀行","兆豐銀行",
    "凱基銀行","國泰世華","中國信託","台北富邦","星展銀行","渣打銀行",
    "滙豐銀行","玉山銀行","台新銀行","遠東商銀","台中銀行","永豐銀行",
    "連線銀行","將來銀行","樂天銀行","安泰銀行","遠東銀行"
]

def get_time_ranges():
    today = datetime.today()
    fmt   = "%Y年%m月%d日"
    return {
        "1m": f"{(today-timedelta(days=30)).strftime(fmt)} ~ {today.strftime(fmt)}",
        "3m": f"{(today-timedelta(days=90)).strftime(fmt)} ~ {today.strftime(fmt)}",
    }

def ask_gemini(prompt, retry=3):
    """呼叫 Gemini，遇到 429 自動等待重試"""
    for attempt in range(retry):
        resp = requests.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60
        )
        if resp.status_code == 429:
            wait = 15 * (attempt + 1)   # 15s / 30s / 45s
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise Exception("Gemini API 超過速率限制，請稍後再試")

def parse_json_array(text):
    text = text.replace("```json", "").replace("```", "").strip()
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1:
        return []
    return json.loads(text[s:e+1])

def query_banks(batch, time_range):
    prompt = f"""針對 {time_range}，整合 PTT/Dcard/Threads 信貸核貸心得。
只針對以下銀行輸出 JSON 陣列（純 JSON，不要 markdown，第一個字元必須是 [）：
{', '.join(batch)}

格式：
[{{"name":"銀行名","tier":1,"rateRange":"2.16%~2.45%","conditions":"條件說明","spec":"核心規格","community":"社群回報（來源：PTT/Dcard，時間）","lowSample":false}}]

tier: 1=APR<2.4%, 2=APR 2.5~2.8%, 3=APR>2.8%
無社群資料則 community 填「無社群實戰數據」，不足2篇 lowSample:true"""
    result = parse_json_array(ask_gemini(prompt))
    time.sleep(5)   # 每批查完等 5 秒，避免 429
    return result

def query_buzz(time_range):
    prompt = f"""{time_range} 台灣信貸市場社群討論度前4名。
只輸出 JSON 陣列（不要 markdown）：
[{{"rank":1,"icon":"👑","bank":"銀行名","reason":"原因40字內","target":"適合誰"}}]"""
    result = parse_json_array(ask_gemini(prompt))
    time.sleep(5)
    return result

def query_summary(time_range):
    prompt = f"{time_range} 台灣信貸市場一句話行情摘要（40字內），只輸出純文字。"
    result = ask_gemini(prompt).strip()
    time.sleep(5)
    return result

def build_report(time_range, label):
    # 一次查全部 23 家（單一大 prompt），減少 API 呼叫次數
    prompt = f"""針對 {time_range}，整合 PTT/Dcard/Threads 信貸核貸心得。
針對以下全部 23 家銀行輸出 JSON 陣列（純 JSON，不要 markdown，第一個字元必須是 [）：
土地銀行、合作金庫、第一銀行、華南銀行、彰化銀行、兆豐銀行、凱基銀行、國泰世華、中國信託、台北富邦、星展銀行、渣打銀行、滙豐銀行、玉山銀行、台新銀行、遠東商銀、台中銀行、永豐銀行、連線銀行、將來銀行、樂天銀行、安泰銀行、遠東銀行

格式：
[{{"name":"銀行名","tier":1,"rateRange":"2.16%~2.45%","conditions":"條件說明","spec":"核心規格（30字內）","community":"社群回報摘要（來源：PTT/Dcard）","lowSample":false}}]

tier: 1=APR<2.4%, 2=APR 2.5~2.8%, 3=APR>2.8%
無社群資料則 community 填「無社群實戰數據」，不足2篇 lowSample:true
23 家全部都要輸出，只輸出 JSON 陣列。"""

    all_banks = parse_json_array(ask_gemini(prompt))
    time.sleep(8)

    def sort_key(b):
        try:
            rate = float(b.get("rateRange","9%").split("~")[0].replace("%","").strip())
        except:
            rate = 9.0
        return (b.get("tier", 2), rate)

    all_banks.sort(key=sort_key)
    for i, b in enumerate(all_banks):
        b["rank"] = i + 1

    buzz    = query_buzz(time_range)
    summary = query_summary(time_range)

    return {
        "label":     label,
        "timeRange": time_range,
        "summary":   summary,
        "banks":     all_banks,
        "buzz":      buzz,
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/query", methods=["POST"])
def query():
    data   = request.json
    email  = data.get("email", "").strip()
    ranges = get_time_ranges()

    try:
        reports = [
            build_report(ranges["1m"], "近一個月"),
            build_report(ranges["3m"], "近三個月"),
        ]

        if email and "@" in email:
            html_body = render_template(
                "email.html",
                reports=reports,
                generated_at=datetime.today().strftime("%Y-%m-%d %H:%M")
            )
            mail.send(Message(
                subject=f"信貸統計表｜近1個月 & 近3個月｜{datetime.today().strftime('%Y-%m-%d')}",
                recipients=[email],
                html=html_body
            ))

        return jsonify({"success": True, "reports": reports})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
