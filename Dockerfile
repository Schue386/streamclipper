FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
ARG ENABLE_ASR=0
RUN if [ "$ENABLE_ASR" = "1" ]; then pip install --no-cache-dir ".[asr]"; else pip install --no-cache-dir .; fi && useradd --create-home appuser
USER appuser
ENV PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
CMD ["python", "streamclipper_os/render_host.py"]
