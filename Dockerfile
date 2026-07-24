FROM node:22-slim AS dashboard-builder
WORKDIR /build/dashboard

RUN corepack enable \
    && corepack prepare pnpm@10.15.1 --activate

COPY dashboard/package.json dashboard/pnpm-lock.yaml dashboard/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY dashboard/ ./
COPY astrbot/core/utils/t2i/template/shiki_runtime.iife.js /build/astrbot/core/utils/t2i/template/shiki_runtime.iife.js
COPY pyproject.toml /build/pyproject.toml
RUN pnpm build \
    && mkdir -p dist/assets \
    && sed -n 's/^version = "\(.*\)"/\1/p' /build/pyproject.toml > dist/assets/version \
    && test -s dist/assets/version

FROM python:3.12-slim
WORKDIR /AstrBot

COPY . /AstrBot/
COPY --from=dashboard-builder /build/dashboard/dist /AstrBot/astrbot/dashboard/dist

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    python3-dev \
    libffi-dev \
    libssl-dev \
    libnspr4 \
    libnss3 \
    libatk-bridge2.0-0t64 \
    libatk1.0-0t64 \
    libatspi2.0-0t64 \
    libcups2t64 \
    libxcomposite1 \
    libxdamage1 \
    ca-certificates \
    bash \
    ffmpeg \
    libavcodec-extra \
    curl \
    gnupg \
    git \
    ripgrep \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN python -m pip install uv \
    && echo "3.12" > .python-version \
    && uv lock \
    && uv export --format requirements.txt --output-file requirements.txt --frozen \
    && uv pip install -r requirements.txt --no-cache-dir --system \
    && uv pip install socksio uv pilk --no-cache-dir --system

EXPOSE 6185

CMD ["python", "main.py"]
