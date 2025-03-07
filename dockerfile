FROM python:3.9-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install additional required packages for Cloud environment
RUN pip install --no-cache-dir google-cloud-storage google-cloud-secret-manager

# Copy only what's needed (avoiding sensitive files)
COPY main.py .
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY setup.py .

# Create scripts directory - removing the problematic copy command
RUN mkdir -p ./scripts/

# Set environment variables for Cloud deployment
ENV PYTHONUNBUFFERED=1
ENV DISCORD_DEPLOYMENT_ENV=production
ENV USE_CLOUD_STORAGE=true

# Command to run the application using Cloud Storage configs
CMD ["python", "main.py", "--config", "cloud"]
