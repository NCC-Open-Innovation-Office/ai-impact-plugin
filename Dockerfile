FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir pydantic

# Copy plugin files
COPY . .

# The plugin is intended to be loaded by Open WebUI.
# In a Docker setup, we can mount this directory into Open WebUI's functions/tools folder.
# Since Open WebUI manages its own environment, this Dockerfile serves as a 
# way to package the plugin files for a volume mount or a custom image.
CMD ["python", "-m", "pytest", "tests/test_impact.py"]
