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
    fmt = "%Y年%m月%d日"
    return {
        "1m": f"{(today-timedelta(days=30)).strftime(fmt)} ~ {today.strftime(fmt)}",
        "3m": f"{(today-timedelta(days=90)).strftime(fmt)} ~ {today.strftime(fmt)}",
    }

def ask_gemini(prompt, retry=5):
    for attempt in range(retry):
        try:
            resp = requests.post(
                GEMINI_URL,
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=180
            )
            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except requests.exceptions.Timeout:
            if attempt == retry - 1:
                raise Exception("Gemini API 回應逾時")
            time.sleep(10)
    raise Exception("Gemini API 速率限制，請稍後再試")

def parse_json_block(text, key):
    """從大 JSON 中取出指定 key 的陣列"""
    marker = f'"{key}"'
    idx = text.find(marker)
    if idx == -1:
        return []
    start = text.find("[", idx)
    if start == -1:
        return []
    depth, end = 0, start
    for i, c in enumerate(text[start:], start):
        if c == "[": depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    try:
        return json.loads(text[start:end+1])
    except:
        return []

def build_reports():
    ranges = get_time_ranges()
    today_str = datetime.today().strftime("%Y年%m月%d日")

    # ── 單一大 prompt，一次呼叫取得所有資料 ──
    prompt = f"""今天是 {today_str}。請整合近3個月台灣 PTT/Dcard/Threads 信貸核貸心得，輸出以下純 JSON（不要 markdown，不要說明文字）：

{{
  "summary1m": "近一個月（{ranges['1m']}）市場摘要，25字內",
  "summary3m": "近三個月（{ranges['3m']}）市場摘要，25字內",
  "banks": [
    {{
      "name": "銀行名",
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
    {{"rank": 1, "icon": "👑", "bank": "銀行名", "reason": "熱議原因30字內", "target": "適合誰"}}
  ]
}}

banks 必須包含全部 23 家：{BANKS_LIST}
tier: 1=APR<2.4%, 2=APR 2.5~2.8%, 3=APR>2.8%
buzz 列前4名
只輸出 JSON，第一個字元是 {{"""

    raw = ask_gemini(prompt)
    raw = raw.replace("```json", "").replace("```", "").strip()

    # 解析整體 JSON
    try:
        s = raw.find("{")
        e = raw.rfind("}")
        data = json.loads(raw[s:e+1])
        all_banks  = data.get("banks", [])
        buzz       = data.get("buzz", [])
        summary_1m = data.get("summary1m", "近一個月台灣信貸市場行情")
        summary_3m = data.get("summary3m", "近三個月台灣信貸市場行情")
    except Exception:
        # fallback：嘗試逐段解析
        all_banks  = parse_json_block(raw, "banks")
        buzz       = parse_json_block(raw, "buzz")
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

    # 近1個月：標注有近期變化的銀行
    banks_1m = []
    for b in all_banks:
        b1 = dict(b)
        change = b.get("recentChange", "無")
        if change and change not in ("無", "無明顯變化", "無變化"):
            b1["community"] = f"【近期】{change}｜{b.get('community','')}"
        banks_1m.append(b1)

    return [
        {"label":"近一個月","timeRange":ranges["1m"],"summary":summary_1m,"banks":banks_1m,"buzz":buzz},
        {"label":"近三個月","timeRange":ranges["3m"],"summary":summary_3m,"banks":all_banks,"buzz":buzz},
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
                "email.html", reports=reports,
                generated_at=datetime.today().strftime("%Y-%m-%d %H:%M")
            )
            mail.send(Message(
                subject=f"信貸統計表｜{datetime.today().strftime('%Y-%m-%d')}",
                recipients=[email],
                html=html_body
            ))
        return jsonify({"success": True, "reports": reports})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
