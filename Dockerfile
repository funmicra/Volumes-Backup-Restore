
# Use a lightweight Python image
FROM python:3.12-slim

# Install docker CLI to access host volumes
RUN apt-get update && \
    apt-get install -y docker.io && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy Python dependencies first for caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy app and environment
COPY backup-manager.py .
COPY .env .

# Default backup directory inside container (override at runtime if needed)
ENV BACKUP_DIR=/backup

# Create backup folder inside container
RUN mkdir -p /backup

# Set entrypoint
ENTRYPOINT ["python3", "backup-manager.py"]
