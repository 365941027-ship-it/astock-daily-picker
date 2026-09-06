FROM python:3.12-slim

WORKDIR /app

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# curl 用于行情网络通道探测（容器内直连东财/新浪）；tzdata 用于北京时间定时
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt || echo "python-docx 可选，跳过"

COPY . .

EXPOSE 8235

CMD ["python", "webapp.py", "--host", "0.0.0.0", "--port", "8235"]
