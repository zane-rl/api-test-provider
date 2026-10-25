ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY server.py ./
COPY scenarios/ ./scenarios/
USER 65532:65532
EXPOSE 18090
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18090/health', timeout=3).close()"]
ENTRYPOINT ["python", "server.py"]
CMD ["--host", "0.0.0.0", "--port", "18090", "--quiet"]
