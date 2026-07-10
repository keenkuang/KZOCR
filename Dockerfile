FROM python:3.12-slim

WORKDIR /app

# 系统依赖（PIL、PyMuPDF 等需要系统库）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxrender1 libxext6 \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir "PyMuPDF>=1.23" "numpy>=1.24" "pillow>=10" \
    "fastapi>=0.110" "uvicorn>=0.29" "jinja2>=3.1" "httpx>=0.27"

# 复制应用
COPY kzocr/ kzocr/

# 数据目录
RUN mkdir -p /app/db /app/trace

EXPOSE 8080

ENV KZOCR_DB_DIR=/app/db
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "kzocr.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
