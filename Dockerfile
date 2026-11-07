FROM node:24-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
# Fabric's optional native canvas dependency differs by OS; npm ci rejects the lock.
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY --from=frontend-build /app/frontend/dist/frontend/browser/ ./static/
ENV PYTHONPATH=/app PORT=8000 HOST=0.0.0.0
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn backend.main:app --host $HOST --port $PORT"]
