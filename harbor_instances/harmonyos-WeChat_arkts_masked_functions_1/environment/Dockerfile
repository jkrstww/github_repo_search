FROM node:20-slim AS masked-repo
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git patch \
    && rm -rf /var/lib/apt/lists/*
RUN git clone 'https://github.com/MaLunan/harmonyos-WeChat.git' /workspace/repo && cd /workspace/repo && git checkout 802be209f5de085434722a96733e0559fcee95b4
COPY error.patch /tmp/error.patch
RUN cd /workspace/repo \
    && git apply /tmp/error.patch \
    && rm -f /tmp/error.patch \
    && rm -rf .git \
    && git init --initial-branch=masked \
    && git config user.name 'ArkTS Benchmark' \
    && git config user.email 'benchmark@localhost' \
    && git add -A \
    && git commit -m 'Masked task baseline'

FROM node:20-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git patch ripgrep python3 python3-pytest \
    && ln -s /usr/bin/python3 /usr/local/bin/python \
    && npm install -g @openai/codex \
    && rm -rf /var/lib/apt/lists/*
COPY --from=masked-repo /workspace/repo /workspace/repo
WORKDIR /workspace/repo
