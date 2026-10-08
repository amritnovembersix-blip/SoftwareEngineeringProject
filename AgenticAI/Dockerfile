# Agentic HTTP service (contracts/agentic-api.yaml). Built from this folder alone, so it works the
# same in the lab (agentic/) and the team repo (AgenticAI/).
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY standards ./standards
# Editable install keeps PACKAGE_ROOT at /app, so default paths (standards/, data/, local/) resolve here.
RUN pip install --no-cache-dir -e .

ENV DATA_DIR=/app/data
EXPOSE 8000
CMD ["python", "-m", "agentic", "serve", "--host", "0.0.0.0", "--port", "8000"]
