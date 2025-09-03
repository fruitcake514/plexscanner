FROM python:3.9-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
ENV PIP_DEFAULT_TIMEOUT=100
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 5555

CMD ["python", "main.py"]
