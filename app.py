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

BANKS_LIST = "土地銀行、合作金庫、第一銀行、華南銀行、彰化銀行、兆豐銀行、凱基銀行、國泰世華、中國信託、台北富邦、星展銀行、渣打銀行、滙豐銀行、玉山銀行、台新銀行、遠東商銀、台中銀行、永豐銀行、連線銀行、將來銀行、樂天銀行、安泰銀行、遠東銀行"

def get_time_ranges():
    today = datetime.today()
    fmt   = "%Y年%m月%d日"
    return {
        "1m": f"{(today-timedelta(days=30)).strftime(fmt)} ~ {today.strftime(fmt)}",
        "3m": f"{(today-timedelta(days=90)).strftime(fmt)} ~ {today.strftime(fmt)}",
    }

def ask_gemini(prompt, retry=4):
    for attempt in range(retry):
        resp = requests.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=120
        )
        if resp.status_code == 429:
            wait = 20 * (attempt + 1)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise Exception("Gemini API 速率限制，請稍後再試")

def parse_json_array(text):
    text = text.replace("```json", "").replace("```", "").strip()
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1:
        return []
    return json.loads(text[s:e+1])

def build_reports():
    """只呼叫 3 次 API，同時產出近1個月和近3個月兩份報告"""
    ranges = get_time_ranges()
    today_str = datetime.today().strftime("%Y年%m月%d日")

    # ── 第1次：查銀行資料（涵蓋近3個月，同時標注近1個月差異）──
    banks_prompt = f"""今天是 {today_str}。
針對近3個月台灣信貸市場，整合 PTT/Dcard/Threads 核貸心得，
輸出以下 23 家銀行的 JSON 陣列（純 JSON，不要 markdown，第一個字元必須是 [）：
{BANKS_LIST}

每筆格式：
{{"name":"銀行名","tier":1,"rateRange":"2.16%~2.45%","conditions":"條件說明","spec":"核心規格（20字內）","community":"社群回報（來源：PTT/Dcard）","recentChange":"近一個月是否有變化，無則填無明顯變化","lowSample":false}}

tier: 1=APR<2.4%, 2=APR 2.5~2.8%, 3=APR>2.8%
只輸出 JSON 陣列，23 家全部包含。"""

    all_banks = parse_json_array(ask_gemini(banks_prompt))
    time.sleep(10)

    # ── 第2次：buzz 排行 ──
    buzz_prompt = f"""近3個月台灣信貸市場，社群討論度前4名。
只輸出 JSON 陣列（不要 markdown）：
[{{"rank":1,"icon":"👑","bank":"銀行名","reason":"原因30字內","target":"適合誰"}}]"""
    buzz = parse_json_array(ask_gemini(buzz_prompt))
    time.sleep(10)

    # ── 第3次：兩段摘要一起產 ──
    summary_prompt = f"""請分別用一句話（各25字內）描述：
1. 近一個月（{ranges['1m']}）台灣信貸市場行情
2. 近三個月（{ranges['3m']}）台灣信貸市場行情

只輸出 JSON：{{"summary1m":"...","summary3m":"..."}}"""
    summary_text = ask_gemini(summary_prompt)
    summary_text = summary_text.replace("```json","").replace("```","").strip()
    try:
        s = summary_text.find("{")
        e = summary_text.rfind("}")
        summaries = json.loads(summary_text[s:e+1])
        summary_1m = summaries.get("summary1m", "近一個月市場行情")
        summary_3m = summaries.get("summary3m", "近三個月市場行情")
    except:
        summary_1m = summary_3m = "台灣信貸市場持續競爭，純網銀低利方案受矚目"

    # 排序
    def sort_key(b):
        try:
            rate = float(b.get("rateRange","9%").split("~")[0].replace("%","").strip())
        except:
            rate = 9.0
        return (b.get("tier", 2), rate)

    all_banks.sort(key=sort_key)
    for i, b in enumerate(all_banks):
        b["rank"] = i + 1

    # 近1個月報告：標注有變化的銀行
    banks_1m = []
    for b in all_banks:
        b1 = dict(b)
        change = b.get("recentChange", "")
        if change and change != "無明顯變化":
            b1["community"] = f"【近期變化】{change}｜{b.get('community','')}"
        banks_1m.append(b1)

    return [
        {
            "label":     "近一個月",
            "timeRange": ranges["1m"],
            "summary":   summary_1m,
            "banks":     banks_1m,
            "buzz":      buzz,
        },
        {
            "label":     "近三個月",
            "timeRange": ranges["3m"],
            "summary":   summary_3m,
            "banks":     all_banks,
            "buzz":      buzz,
        },
    ]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/query", methods=["POST"])
def query():
    data  = request.json
    email = data.get("email", "").strip()

    try:
        reports = build_reports()

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
