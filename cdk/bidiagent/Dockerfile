FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app

# pyaudio build に必要なもの（assert.h を含む）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv
ENV UV_SYSTEM_PYTHON=1 UV_COMPILE_BYTECODE=1

COPY requirements.txt requirements.txt
RUN uv pip install --system -r requirements.txt
# 追加：pyaudio が入ってないならここでビルドを落とす
RUN python -c "import pyaudio; print('pyaudio ok:', pyaudio.__version__)"
RUN uv pip install --system "aws-opentelemetry-distro>=0.10.1"

ENV AWS_REGION=ap-northeast-1
ENV AWS_DEFAULT_REGION=ap-northeast-1
ENV DOCKER_CONTAINER=1

RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

COPY . .
EXPOSE 8080

CMD ["opentelemetry-instrument", "python", "-m", "agent"]
