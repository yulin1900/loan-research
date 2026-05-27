import os
import json
import google.generativeai as genai
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from flask_mail import Mail, Message

app = Flask(__name__)

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')
mail = Mail(app)

genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-1.5-flash')

BANKS = [
    "土地銀行","合作金庫","第一銀行","華南銀行","彰化銀行","兆豐銀行",
    "凱基銀行","國泰世華","中國信託","台北富邦","星展銀行","渣打銀行",
    "滙豐銀行","玉山銀行","台新銀行","遠東商銀","台中銀行","永豐銀行",
    "連線銀行","將來銀行","樂天銀行","安泰銀行","遠東銀行"
]

def get_time_ranges():
    today = datetime.today()
    fmt = "%Y年%m月%d日"
    return {
        "1m": f"{(today - timedelta(days=30)).strftime(fmt)} ~ {today.strftime(fmt)}",
        "3m": f"{(today - timedelta(days=90)).strftime(fmt)} ~ {today.strftime(fmt)}",
    }

def ask_gemini(prompt):
    response = model.generate_content(prompt)
    return response.text

def query_banks(batch, time_range):
    prompt = f"""針對 {time_range}，整合 PTT/Dcard/Threads 信貸核貸心得。
只針對以下銀行輸出 JSON 陣列（純 JSON，第一個字元必須是 [，不要有任何說明文字）：
{', '.join(batch)}

格式：
[{{"name":"銀行名","tier":1,"rateRange":"2.16%~2.45%","conditions":"條件說明","spec":"核心規格","community":"社群回報（來源：PTT/Dcard，時間）","lowSample":false}}]

tier: 1=APR<2.4%, 2=APR 2.5~2.8%, 3=APR>2.8%
無社群資料則 community 填「無社群實戰數據」，不足2篇 lowSample:true
只輸出 JSON 陣列，不要加 ```json 或任何 markdown。"""

    text = ask_gemini(prompt)
    # 清除可能的 markdown
    text = text.replace('```json', '').replace('```', '').strip()
    s, e = text.find('['), text.rfind(']')
    if s == -1 or e == -1:
        return []
    return json.loads(text[s:e+1])

def query_buzz(time_range):
    prompt = f"""{time_range} 台灣信貸市場，社群討論度前4名。
只輸出JSON陣列（不要加 ```json 或任何說明）：
[{{"rank":1,"icon":"👑","bank":"銀行名","reason":"原因（40字內）","target":"適合誰"}}]"""
    text = ask_gemini(prompt)
    text = text.replace('```json', '').replace('```', '').strip()
    s, e = text.find('['), text.rfind(']')
    if s == -1 or e == -1:
        return []
    return json.loads(text[s:e+1])

def query_summary(time_range):
    prompt = f"{time_range} 台灣信貸市場一句話行情摘要（40字內），只輸出純文字，不要任何標點符號以外的格式。"
    return ask_gemini(prompt).strip()

def build_report(time_range, label):
    batches = [BANKS[i:i+6] for i in range(0, len(BANKS), 6)]
    all_banks = []
    for batch in batches:
        try:
            rows = query_banks(batch, time_range)
            all_banks.extend(rows)
        except Exception:
            continue

    def sort_key(b):
        tier = b.get('tier', 2)
        try:
            rate = float(b.get('rateRange', '9%').split('~')[0].replace('%','').strip())
        except:
            rate = 9.0
        return (tier, rate)

    all_banks.sort(key=sort_key)
    for i, b in enumerate(all_banks):
        b['rank'] = i + 1

    return {
        "label":     label,
        "timeRange": time_range,
        "summary":   query_summary(time_range),
        "banks":     all_banks,
        "buzz":      query_buzz(time_range),
    }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/query', methods=['POST'])
def query():
    data  = request.json
    email = data.get('email', '').strip()
    ranges = get_time_ranges()

    try:
        report_1m = build_report(ranges['1m'], '近一個月')
        report_3m = build_report(ranges['3m'], '近三個月')
        reports   = [report_1m, report_3m]

        if email and '@' in email:
            html_body = render_template(
                'email.html',
                reports=reports,
                generated_at=datetime.today().strftime('%Y-%m-%d %H:%M')
            )
            msg = Message(
                subject=f"信貸統計表｜近1個月 & 近3個月｜{datetime.today().strftime('%Y-%m-%d')}",
                recipients=[email],
                html=html_body
            )
            mail.send(msg)

        return jsonify({'success': True, 'reports': reports})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
