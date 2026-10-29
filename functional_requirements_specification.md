# Functional Requirements Specification (FRS)
**Project Name:** Brother Color Label Editor Web App (VC-500W Web Equivalent)  
**Target Device:** Brother VC-500W Full-Color Label Printer  
**Document Version:** 1.0.0  

---

## 1. Executive Summary
This document specifies the functional requirements for a custom cross-platform web application designed to replicate and modernize the core functionality of the **Brother Color Label Editor 2** app for the Brother VC-500W network label printer.

---

## 2. Functional Requirements Matrix

### 2.1 Printer Discovery & Status Monitoring
* **FR-DISC-01 (Auto Discovery):** The system must automatically discover Brother VC-500W printers present on the local local area network (LAN/Wi-Fi) using mDNS (Bonjour/ZeroConf) and network broadcast protocols.
* **FR-DISC-02 (Manual IP Connect):** The user must be able to manually enter an IPv4 address or hostname to connect directly to a printer.
* **FR-DISC-03 (Tape Identification):** The system must query the printer to detect the currently mounted ZINK roll cassette type, return its width (9mm, 12mm, 19mm, 25mm, or 50mm), and detect whether a cleaning tape (CK-1000) or print tape (CZ-100x) is loaded.
* **FR-DISC-04 (Status Alerts):** The system must report printer states (Ready, Printing, Paper Jam, Out of Tape, Cover Open, Busy).

---

### 2.2 Design Canvas & Object Management
* **FR-CANV-01 (Adaptive Canvas):** The design editor canvas must dynamically adjust its visual proportions based on the mounted tape width and chosen label length (up to 432mm / 17 inches).
* **FR-OBJ-01 (Text Layer Placement):** Users must be able to add, position, drag, resize, rotate, and delete multiple independent text layers on the canvas.
* **FR-OBJ-02 (Text Formatting Options):**
  * **Font Family:** Selectable system and custom web fonts.
  * **Font Size & Scale:** Manual point size and visual drag-resize.
  * **Font Weight & Style:** Bold, Italic, Underline, Strikethrough.
  * **Alignment:** Left, Center, Right, Justified.
  * **Color:** Full 24-bit RGB color picker for text and text background/highlighting.
  * **Orientation:** 0°, 90°, 180°, and 270° orientation switches.
  * **Stroke & Thickness:** Text outline color and stroke weight controls.
* **FR-OBJ-03 (Image Layer Management):**
  * Import custom raster images (PNG, JPEG, WebP, SVG).
  * Scale/Resize, crop, flip (horizontal/vertical), rotate, and re-order layer depth (Bring Forward, Send Backward).
* **FR-OBJ-04 (Canvas Backgrounds & Shapes):**
  * Support custom background fill colors, gradients, basic vector shapes (rectangles, circles, lines), and frames/borders.

---

### 2.3 Label Persistence & CRUD Operations
* **FR-CRUD-01 (Add New Label):** Users can start a blank canvas with a selected roll width preset or auto-matched width.
* **FR-CRUD-02 (Save to Local Storage):** Label designs (canvas layout, JSON representation, and preview thumbnail) must be persisted locally on the client device without requiring cloud authentication.
* **FR-CRUD-03 (List & Reopen Saved Labels):** A library gallery view must list saved design cards showing thumbnails, dimensions, and modified dates. Clicking opens the design state back into the active canvas.
* **FR-CRUD-04 (Edit & Duplicate):** Users can modify existing designs and save changes directly or export as a copy.
* **FR-CRUD-05 (Delete Labels):** Users can permanently delete label designs individually or via batch selection.

---

### 2.4 Print Execution
* **FR-PRNT-01 (Raster Generation):** The system must render the final canvas into a 313 DPI full-color uncompressed image matching target pixel measurements for the continuous tape.
* **FR-PRNT-02 (Print Job Submission):** The system must send binary commands to the target printer with parameters for tape cut selection (Full Cut, Half Cut, No Cut).
* **FR-PRNT-03 (Progress Feedback):** Real-time print job transmission progress bar and completion confirmation.