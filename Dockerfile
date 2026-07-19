FROM python:3.12-slim

WORKDIR /app

# 安装 Python 依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir "PyMuPDF>=1.23" "numpy>=1.24" "pillow>=10" \
    "fastapi>=0.110" "uvicorn>=0.29" "jinja2>=3.1" "httpx>=0.27" "python-multipart>=0.0.9" \
    "celery>=5.3,<6" "redis>=5.0"

# 复制应用
COPY kzocr/ kzocr/

# 数据目录
RUN mkdir -p /app/db /app/trace && chown -R 1000:1000 /app/db /app/trace

# 非 root 运行
USER 1000:1000

EXPOSE 8080

ENV KZOCR_DB_DIR=/app/db
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "kzocr.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
