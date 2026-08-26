# 钉在 bookworm(Debian 12)：不带后缀的 slim 已指向 trixie(Debian 13)，用户态过新，
# 在 CentOS 7 老内核 + 老 libseccomp 的部署机上会因未知系统调用被 seccomp 拒绝(EPERM)
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /uvx /bin/

# docker-cli：strix 沙箱通过宿主机 docker.sock 启动分析容器（socket 由 compose 挂载）
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    git fonts-wqy-microhei fonts-wqy-zenhei docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
