# 信貸統計表調查系統

整合 PTT / Dcard / Threads 社群輿情，產出台灣 23 家銀行信貸統計報告。

## 檔案結構

```
loan-research/
├── app.py              # Flask 主程式
├── requirements.txt    # Python 套件
├── Procfile            # Render 部署設定
├── .env.example        # 環境變數範本
├── .gitignore
└── templates/
    ├── index.html      # 前端介面
    └── email.html      # 寄信模板
```

## 本機測試

```bash
# 1. 安裝套件
pip install -r requirements.txt

# 2. 建立 .env 檔
cp .env.example .env
# 填入你的 ANTHROPIC_API_KEY 和 Gmail 設定

# 3. 執行
python app.py
# 開啟 http://localhost:5000
```

## 部署到 Render（免費）

請見下方 Claude 的部署教學。

## 環境變數說明

| 變數名 | 說明 |
|--------|------|
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `MAIL_USERNAME` | Gmail 帳號 |
| `MAIL_PASSWORD` | Gmail App 密碼（非登入密碼） |
