# The sandbox image for Python projects: an interpreter and pytest, nothing else.
# Build: docker build -t agentswarm/sandbox-python:3.13 -f docker/sandbox-python.Dockerfile docker
FROM python:3.13-slim
RUN pip install --no-cache-dir "pytest>=8,<10"
