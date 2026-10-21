FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Seed the persistent skills volume with the bundled playbooks
RUN useradd --create-home muis && mkdir -p /data/skills \
    && cp app/skills/*.md /data/skills/ && chown -R muis /data /srv
USER muis

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
