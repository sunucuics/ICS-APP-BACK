# Use a lightweight Python base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (if any needed, e.g., for Firebase or Pillow for image handling)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Copy requirements and install (using pip for simplicity; could use Poetry as well)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY app ./app

# Copy the Firebase service account key if needed (for container, one might mount it or use secret manager)
# For security, we assume credentials will be provided via secure means, not baked into image
# COPY path/to/firebase_key.json /app/firebase_key.json

# Expose the port (if running directly in container)
EXPOSE 8000

# Default command to run the app with Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
