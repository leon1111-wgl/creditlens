FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CREDITLENS_STATE_DIR=/state
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 10001 --create-home creditlens
COPY creditlens ./creditlens
COPY data/examples ./data/examples
RUN mkdir /state && chown creditlens:creditlens /state
USER creditlens
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=2)"
CMD ["python","-m","uvicorn","creditlens.api:app","--host","0.0.0.0","--port","8765"]
