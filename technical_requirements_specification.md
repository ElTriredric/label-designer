# Technical Requirements Specification (TRS)
**Project Name:** Brother Color Label Editor Web App (VC-500W Web Equivalent)  
**Technology Stack:** Angular (Frontend), Python (Backend), Docker & GitHub Actions (CI/CD)  
**Document Version:** 1.0.0  

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Client Browser                      │
│   ┌─────────────────────────────────────────────────┐   │
│   │  Angular SPA (SCSS + Angular Material CDK)      │   │
│   │  - Canvas Rendering Engine (HTML5 Canvas)       │   │
│   │  - IndexedDB Local Storage Manager              │   │
│   └────────────────────────┬────────────────────────┘   │
└────────────────────────────┼────────────────────────────┘
                             │ HTTP / WebSockets
┌────────────────────────────┼────────────────────────────┐
│                    Docker Container                      │
│   ┌────────────────────────▼────────────────────────┐   │
│   │  Python Backend (FastAPI / AsyncIO)             │   │
│   │  - mDNS Scanner (zeroconf)                      │   │
│   │  - Brother VC-500W Binary Protocol Engine       │   │
│   └────────────────────────┬────────────────────────┘   │
└────────────────────────────┼────────────────────────────┘
                             │ Raw TCP Sockets (Port 9100)
                             ▼
              [ Brother VC-500W Label Printer ]
```

---

## 2. Frontend Technical Requirements (Angular)

* **Framework:** Angular 22.
* **UI Components:** **Angular Material CDK ONLY** (`@angular/cdk`) for accessibility, overlays, drag-and-drop (`@angular/cdk/drag-drop`), and dialogs.
* **Styling Strict Constraint:** **NO external CSS framework** (e.g., Tailwind, Bootstrap, or Material Theme CSS). All styling must be written from scratch using modular **SCSS** with CSS Custom Properties (variables) for design tokens.
* **Canvas Engine:** HTML5 2D Canvas context / Fabric.js wrapper optimized for high-DPI export (313 DPI rendering engine).
* **Local Storage:** Client-side storage implemented using `IndexedDB` (via `idb` or RxDB) to store high-resolution thumbnails and canvas JSON state locally without server dependency.

---

## 3. Backend Technical Requirements (Python)

* **Language & Runtime:** Python 3.11+.
* **Web Framework:** FastAPI (Asynchronous execution with `asyncio` and `uvicorn`).
* **Printer Communication Stack:**
  * **Network Discovery:** `zeroconf` Python package to listen for mDNS (`_ipp._tcp.local.`, `_printer._tcp.local.`, or Brother specific UDP broadcasts).
  * **Protocol Engine:** Custom TCP stream writer emitting Brother VC-500W BCL (Brother Command Language) / Raster commands over port `9100`.
  * **Image Processing:** `Pillow (PIL)` for image scaling, color profile conversion (RGB 24-bit), and pixel-line bitmasking required by VC-500W 313 DPI thermal engine.

---

## 4. CI/CD & Deployment Requirements

### 4.1 Containerization (Docker & Docker Compose)
* **Multi-stage Dockerfile:** Build Angular static files, compile Python dependencies, and serve static assets alongside FastAPI via `uvicorn`.
* **Container Base:** `python:3.11-slim` or `alpine` equivalent.

### 4.2 GitHub Actions Pipeline
* Pipeline triggers on pushes to `main` branch.
* Pipeline builds multi-arch Docker image (`linux/amd64`, `linux/arm64` for Raspberry Pi support).
* Authenticates and pushes images directly to **GitHub Container Registry (GHCR)** (`ghcr.io`).