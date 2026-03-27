FROM public.ecr.aws/docker/library/python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py .
EXPOSE 8080
# MEMORY_ID must be set at deploy time via environment variable
CMD ["python", "agent.py"]
