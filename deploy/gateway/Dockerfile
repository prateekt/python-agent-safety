FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .

RUN useradd -r -u 10001 gateway
USER gateway

EXPOSE 8765
CMD ["python", "-m", "agent_safety.gateway.server"]
