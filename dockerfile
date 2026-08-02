# 1. Base Image: Use a slim Python Linux environment
FROM python:3.12-slim-bookworm

# 2. Prevent Python from buffering stdout/stderr (crucial for live real-time tracing & logs!)
ENV PYTHONUNBUFFERED=1

# 3. Install the high-speed 'uv' package manager directly from Astral's official release image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 4. Set our clean working directory inside the container
WORKDIR /app

# 5. CACHE OPTIMIZATION LAYER: Copy ONLY dependencies first!
# Because code changes frequently while dependencies change rarely, downloading packages 
# in this isolated step allows Docker to cache your installed AI libraries between builds.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 6. COPY SOURCE CODE: Now bring over your actual project files.
# (Thanks to our .dockerignore, your .env secrets will NOT be copied!)
COPY . /app

# 7. Network & Execution
# We expose port 3000 for your Streamlit UI
EXPOSE 3000

# 8. CRITICAL DOCKER GOTCHA: You MUST tell Streamlit to bind to 0.0.0.0 inside a container!
# If Streamlit binds to 127.0.0.1 (localhost), it won't be reachable outside the container.
ENTRYPOINT ["uv", "run", "streamlit", "run", "streamlit_app.py", "--server.port=3000", "--server.address=0.0.0.0"]
