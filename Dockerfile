# 동토리 MCP: PlayMCP in KC 배포용. ★빌드는 반드시 linux/amd64:
#   docker buildx build --platform linux/amd64 -t dongtori-mcp .
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY regions.py region_resolver.py backend.py render.py server.py ./
COPY data/ ./data/

ENV PORT=8080 TZ=Asia/Seoul
EXPOSE 8080
CMD ["python", "server.py"]
