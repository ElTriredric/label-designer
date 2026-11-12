version: '3.8'

services:
  vc500w-label-app:
    image: ghcr.io/${GITHUB_REPOSITORY:-user/brother-label-editor}:${IMAGE_TAG:-latest}
    build:
      context: .
      dockerfile: Dockerfile
    container_name: brother_color_label_app
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - PORT=8000
      - HOST=0.0.0.0
      - LOG_LEVEL=info
    volumes:
      - app_data:/app/data

volumes:
  app_data: