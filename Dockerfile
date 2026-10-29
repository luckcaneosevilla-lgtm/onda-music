FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
# JS runtime for yt-dlp: without it only one YouTube client works, and YouTube often
# refuses that client's streams from datacenter IPs. yt-dlp[default] brings yt-dlp-ejs.
COPY --from=denoland/deno:bin-2.1.4 /deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "server.py", "serve", "--port", "8000"]
