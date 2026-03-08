# 使用輕量級 Python 映像檔
FROM python:3.12-slim

WORKDIR /app

# 複製依賴並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製程式碼
COPY . .

# 啟動命令 (注意 host 必須是 0.0.0.0 才能被外部訪問)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]