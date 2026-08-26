FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

WORKDIR /app

ENV UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/
# Copy the project first. If uv.lock has been generated locally and is present
# in the checkout, uv sync will automatically use it.
COPY . .
RUN uv sync --no-dev

RUN mkdir -p /data
ENV UIPM_DATA_DIR=/data
ENV PORT=8080

EXPOSE 8080
VOLUME ["/data"]

# Dependencies are already synchronized during the image build.
CMD ["uv", "run", "--no-sync", "python", "-m", "app.main"]
