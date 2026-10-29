FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
# JS runtime for yt-dlp: without it only one YouTube client works, and YouTube often
# refuses that client's streams from datacenter IPs. yt-dlp[default] brings yt-dlp-ejs.
COPY --from=denoland/deno:bin-2.3.3 /deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Fail the build instead of deploying an unsupported/missing challenge runtime.
RUN deno --version && python -c "import yt_dlp, yt_dlp_ejs; import subprocess; v=subprocess.check_output(['deno','--version'],text=True).split()[1]; assert tuple(map(int,v.split('.')[:2])) >= (2,3), v"
COPY . .
EXPOSE 8000
CMD ["python", "server.py", "serve", "--port", "8000"]
