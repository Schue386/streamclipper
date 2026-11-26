FROM python:3.12-slim
COPY --from=denoland/deno:bin-2.4.5 /deno /usr/local/bin/deno
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg tzdata rclone && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
ARG ENABLE_ASR=1
RUN if [ "$ENABLE_ASR" = "1" ]; then pip install --no-cache-dir ".[asr]"; else pip install --no-cache-dir .; fi && useradd --create-home appuser
RUN if [ "$ENABLE_ASR" = "1" ]; then python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-tiny', local_dir='/opt/whisper-tiny', allow_patterns=['model.bin','config.json','tokenizer.json','vocabulary.*','preprocessor_config.json'])"; fi
RUN python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Europe/Berlin'))"
RUN rclone version
USER appuser
ENV ASR_MODEL_DIR=/opt/whisper-tiny
ENV PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
CMD ["python", "streamclipper_os/render_host.py"]
