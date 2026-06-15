FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 系统依赖：trafilatura/lxml、sentence-transformers 编译可能用到
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用层缓存）
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY . .

EXPOSE 8000

# 默认启动 web；调度/seed 用 docker-compose 的其它入口
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
