FROM python:3.9-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .
COPY config.json .
RUN test -f ignore.json || echo "[]" > ignore.json

EXPOSE 5000

CMD ["python", "main.py"]
