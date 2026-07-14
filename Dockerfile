FROM python:3.11-slim

# Install system dependencies required by the Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    libcairo2-dev \
    libjpeg-dev \
    libgif-dev \
    libgirepository-2.0-dev \
    gir1.2-glib-2.0 \
    python3-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY src /app/src

# Copy the configuration file
COPY conf/config_rpi.json /app/conf/config.json

# Set Python path so imports work correctly
ENV PYTHONPATH=/app

# Run the application
CMD ["python", "src/main.py"]
# Run forever to keep the container alive for testing
#CMD ["tail", "-f", "/dev/null"]