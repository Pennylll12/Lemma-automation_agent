FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend backend
COPY dist dist
RUN useradd --uid 10001 --create-home flow && mkdir -p /app/data && chown flow /app/data
USER flow
EXPOSE 8000
CMD ["uvicorn","backend.app:app","--host","0.0.0.0","--port","8000"]
