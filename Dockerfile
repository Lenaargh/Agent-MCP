FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOST=0.0.0.0

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Install the package itself in editable mode
RUN pip install -e .

# Expose the port the app runs on
EXPOSE 8080

# Run the MCP server
# Using uvicorn directly or the CLI module
CMD ["python", "-m", "agent_mcp.cli", "--port", "8080", "--transport", "sse"]
