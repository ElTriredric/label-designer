import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, ElementRef, HostListener, NgZone, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CdkDragDrop, DragDropModule, moveItemInArray } from '@angular/cdk/drag-drop';
import { Dialog } from '@angular/cdk/dialog';
import { TranslocoPipe } from '@jsverse/transloco';
import { LanguageService } from '../i18n/language.service';
import { firstValueFrom } from 'rxjs';
import { PrintConfirmation, PrintConfirmDialogComponent } from './print-confirm-dialog.component';
import type { fabric as FabricTypes } from 'fabric';
declare const fabric: typeof FabricTypes;
import { deleteLabel, listLabels, saveLabel, SavedLabel } from './label-store';

const UNITS_PER_MM = 10;
const DPI = 313;
const WIDTHS = [9, 12, 19, 25, 50];
const SUBNET_KEY = 'label-designer.scanSubnet';
type Shape = FabricTypes.Object & { cropX?: number; cropY?: number; flipX?: boolean; flipY?: boolean; fontWeight?: string; fontStyle?: string; underline?: boolean; linethrough?: boolean };

@Component({
  selector: 'app-canvas',
  standalone: true,
  imports: [CommonModule, FormsModule, DragDropModule, TranslocoPipe],
  templateUrl: './canvas.component.html',
  styleUrl: './canvas.component.scss'
})
export class CanvasComponent implements OnInit, OnDestroy {
  @ViewChild('canvasElement', { static: true }) canvasRef!: ElementRef<HTMLCanvasElement>;
  @ViewChild('workspace', { static: true }) workspaceRef!: ElementRef<HTMLElement>;
  @ViewChild('surface', { static: true }) surfaceRef!: ElementRef<HTMLElement>;
  private canvas!: FabricTypes.Canvas;
  private observer?: ResizeObserver;
  private destroyed = false;
  private copyPromise: Promise<FabricTypes.Object[]> | null = null;
  private pasteCount = 0;
  private draggingMarker: number | null = null;
  selectedMarker: number | null = null;
  cutPositionsMm: number[] = [];
  hasCopy = false;
  widths = WIDTHS;
  widthMm = 12;
  lengthMm = 60;
  name = '';
  id: string | null = null;
  labels: SavedLabel[] = [];
  selectedIds = new Set<string>();
  selected: Shape | null = null;
  color = '#222222';
  background = '#ffffff';
  font = 'Arial';
  fontSize = 24;
  stroke = '#000000';
  strokeWidth = 0;
  align = 'left';
  cut = 'full';
  printers: { ip: string; hostname: string; source?: string; verified?: boolean }[] = [];
  scanning = false;
  scanSubnet = CanvasComponent.storedSubnet();
  printer = '';
  manualAddress = '';
  statusKey = '';
  statusParams: Record<string, string | number> = {};
  errorKey = '';
  errorParams: Record<string, string | number> = {};
  busy = false;
  progress = 0;
  printStage = '';
  jobSent = 0;
  jobSegments = 1;

  constructor(private readonly zone: NgZone, private readonly changeDetector: ChangeDetectorRef,
              private readonly dialog: Dialog, public readonly language: LanguageService) {
    this.name = language.translate('design.untitled');
  }

  changeLanguage(value: string): void {
    const oldDefault = this.language.translate('design.untitled');
    this.language.setLanguage(value);
    if (!this.id && this.name === oldDefault) this.name = this.language.translate('design.untitled');
    if (this.statusParams['tapeType']) {
      this.statusParams = { ...this.statusParams,
        tape: this.language.translate('printer.tape.' + this.statusParams['tapeType']) };
    }
    if (this.statusParams['stateCode']) {
      this.statusParams = { ...this.statusParams,
        state: this.language.translate('printer.status.' + this.statusParams['stateCode']) };
    }
    if (this.errorParams['stateCode']) {
      this.errorParams = { ...this.errorParams,
        state: this.language.translate('printer.status.' + this.errorParams['stateCode']) };
    }
  }

  private setStatus(key: string, params: Record<string, string | number> = {}): void {
    this.statusKey = key;
    this.statusParams = params;
  }
  private setError(key: string, params: Record<string, string | number> = {}): void {
    this.errorKey = key;
    this.errorParams = params;
  }

  private printerParams(result: { tape_type?: string; tape_width_mm?: number; status?: string }) {
    const tapeType = ['Print Tape', 'Cleaning Tape'].includes(result.tape_type || '') ? result.tape_type! : 'Unknown';
    const knownStates = ['IDLE', 'READY', 'PRINTING', 'FEEDING', 'CUTTING', 'ERROR', 'UNKNOWN', 'UNREACHABLE', 'BUSY', 'COVER_OPEN', 'PAPER_JAM', 'OUT_OF_TAPE'];
    const stateCode = knownStates.includes(result.status || '') ? result.status! : 'UNKNOWN';
    return { tapeType, stateCode, width: result.tape_width_mm ?? '?',
      tape: this.language.translate('printer.tape.' + tapeType),
      state: this.language.translate('printer.status.' + stateCode) };
  }

  private printError(message: string): void {
    const mismatch = message.match(/Loaded tape is (\d+) mm, design is (\d+) mm/);
    if (mismatch) { this.setError('errors.tapeMismatch', { loaded: mismatch[1], design: mismatch[2] }); return; }
    const notReady = message.match(/Printer not ready: ([A-Z]+)/);
    if (notReady) {
      const stateCode = ['IDLE', 'READY', 'PRINTING', 'FEEDING', 'CUTTING', 'ERROR', 'BUSY'].includes(notReady[1]) ? notReady[1] : 'UNKNOWN';
      this.setError('errors.notReady', { stateCode, state: this.language.translate('printer.status.' + stateCode) });
      return;
    }
    if (message.includes('Another print job')) this.setError('errors.busy');
    else if (message.includes('Cannot confirm a print tape')) this.setError('errors.unknownTape');
    else if (message.includes('Invalid label dimensions')) this.setError('errors.invalidDimensions');
    else if (message.includes('Cut markers require')) this.setError('errors.cutMode');
    else if (message.includes('Each segment must be at least')) this.setError('errors.cutSpacing');
    else if (message.includes('Invalid cut mode')) this.setError('errors.invalidCut');
    else if (message.includes('Expected ') && message.includes(' pixels')) this.setError('errors.invalidRaster');
    else if (message.includes('Print failed') || message.includes('Printer error')) this.setError('errors.printerFailed');
    else this.setError('errors.print');
  }

  ngOnInit(): void {
    this.canvas = new fabric.Canvas(this.canvasRef.nativeElement, {
      backgroundColor: this.background, preserveObjectStacking: true
    });
    this.canvas.on('selection:created', () => this.syncSelection());
    this.canvas.on('selection:updated', () => this.syncSelection());
    this.canvas.on('selection:cleared', () => this.syncSelection());
    // Canvas pointer handlers may run outside Angular's change-detection zone.
    // Mouse-up also covers reselecting a remaining object after removing another.
    this.canvas.on('mouse:up', () => this.syncSelection());
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(this.workspaceRef.nativeElement);
    this.resize();
    void this.refreshLibrary();
    void this.refreshPrinters();
  }
  ngOnDestroy(): void { this.destroyed = true; this.observer?.disconnect(); this.canvas.dispose(); }

  private syncSelection(): void {
    this.zone.run(() => {
      this.updateSelection();
      if (this.selected) this.selectedMarker = null;
      this.changeDetector.detectChanges();
    });
  }

  private updateSelection(): void {
    this.selected = this.canvas.getActiveObject() as Shape | null;
    if (!this.selected) return;
    this.color = typeof this.selected.fill === 'string' ? this.selected.fill : '#222222';
    this.font = (this.selected as FabricTypes.Text).fontFamily || 'Arial';
    this.fontSize = (this.selected as FabricTypes.Text).fontSize || 24;
    this.stroke = typeof this.selected.stroke === 'string' ? this.selected.stroke : '#000000';
    this.strokeWidth = this.selected.strokeWidth || 0;
    this.align = (this.selected as FabricTypes.Textbox).textAlign || 'left';
  }
  resize(): void {
    if (!this.canvas) return;
    const logicalWidth = this.lengthMm * UNITS_PER_MM;
    const logicalHeight = this.widthMm * UNITS_PER_MM;
    const scale = Math.min(1.5, (this.workspaceRef.nativeElement.clientWidth - 32) / logicalWidth);
    const displayWidth = Math.round(logicalWidth * scale);
    const displayHeight = Math.round(logicalHeight * scale);
    this.canvas.setDimensions({ width: displayWidth, height: displayHeight });
    this.surfaceRef.nativeElement.style.width = `${displayWidth}px`;
    this.surfaceRef.nativeElement.style.height = `${displayHeight}px`;
    this.canvas.setZoom(scale);
    this.canvas.requestRenderAll();
  }
  dimensionsChanged(): void {
    this.widthMm = WIDTHS.includes(+this.widthMm) ? +this.widthMm : 12;
    this.lengthMm = Math.max(10, Math.min(432, Number(this.lengthMm) || 60));
    this.normalizeCutPositions();
    this.resize();
  }

  private normalizeCutPositions(): void {
    const valid: number[] = [];
    for (const value of [...this.cutPositionsMm].sort((a, b) => a - b)) {
      if (Number.isFinite(value) && value - (valid.at(-1) ?? 0) >= 10 &&
          this.lengthMm - value >= 10 && valid.length < 15) valid.push(value);
    }
    this.cutPositionsMm = valid;
    this.selectedMarker = null;
  }

  get canAddCutMarker(): boolean {
    if (this.cutPositionsMm.length >= 15) return false;
    const edges = [0, ...this.cutPositionsMm, this.lengthMm];
    return edges.some((right, index) => index > 0 && right - edges[index - 1] >= 20);
  }

  addCutMarker(): void {
    if (!this.canAddCutMarker) return;
    const edges = [0, ...this.cutPositionsMm, this.lengthMm];
    let gapIndex = 0;
    for (let index = 1; index < edges.length; index++) {
      if (edges[index] - edges[index - 1] > edges[gapIndex + 1] - edges[gapIndex]) gapIndex = index - 1;
    }
    const position = Math.round((edges[gapIndex] + edges[gapIndex + 1]) * 5) / 10;
    this.cutPositionsMm.splice(gapIndex, 0, position);
    this.selectedMarker = gapIndex;
    if (this.cut === 'none') { this.cut = 'full'; this.setStatus('cuts.fullDefault'); }
  }

  setCutMarker(index: number, position: number): boolean {
    const before = index ? this.cutPositionsMm[index - 1] : 0;
    const after = index + 1 < this.cutPositionsMm.length ? this.cutPositionsMm[index + 1] : this.lengthMm;
    if (!Number.isFinite(position) || position - before < 10 - 1e-6 || after - position < 10 - 1e-6) {
      this.setError('errors.cutSpacing');
      return false;
    }
    this.cutPositionsMm[index] = Math.round(position * 10) / 10;
    this.setError('');
    return true;
  }

  markerInputChanged(index: number, event: Event): void {
    const input = event.target as HTMLInputElement;
    if (!this.setCutMarker(index, Number(input.value))) input.value = String(this.cutPositionsMm[index]);
  }

  markerPointerDown(index: number, event: PointerEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.canvas.discardActiveObject();
    this.selectedMarker = index;
    this.draggingMarker = index;
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    this.changeDetector.detectChanges();
  }

  markerPointerMove(index: number, event: PointerEvent): void {
    if (this.draggingMarker !== index) return;
    const bounds = this.surfaceRef.nativeElement.getBoundingClientRect();
    const before = index ? this.cutPositionsMm[index - 1] : 0;
    const after = index + 1 < this.cutPositionsMm.length ? this.cutPositionsMm[index + 1] : this.lengthMm;
    const position = Math.max(before + 10, Math.min(after - 10,
      (event.clientX - bounds.left) / bounds.width * this.lengthMm));
    this.setCutMarker(index, position);
    this.changeDetector.detectChanges();
  }

  markerPointerUp(event: PointerEvent): void {
    this.draggingMarker = null;
    const element = event.currentTarget as HTMLElement;
    if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
  }

  removeCutMarker(index: number): void {
    if (index < 0 || index >= this.cutPositionsMm.length) return;
    this.cutPositionsMm.splice(index, 1);
    this.selectedMarker = null;
    this.changeDetector.detectChanges();
  }

  addText(): void {
    const text = new fabric.Textbox(this.language.translate('design.editText'), {
      left: 20, top: 20, width: 180, fontSize: 24, fill: '#222222', fontFamily: 'Arial'
    });
    this.canvas.add(text); this.canvas.setActiveObject(text);
    this.updateSelection(); this.canvas.requestRenderAll();
    text.enterEditing(); text.selectAll();
  }
  addShape(type: 'rect' | 'circle' | 'line' | 'frame'): void {
    const common = { left: 20, top: 20, fill: this.color, stroke: this.stroke, strokeWidth: this.strokeWidth };
    const shape = type === 'circle' ? new fabric.Circle({ ...common, radius: 25 })
      : type === 'line' ? new fabric.Line([20, 20, 140, 20], { stroke: this.color, strokeWidth: 3 })
      : new fabric.Rect({ ...common, width: 100, height: 55,
          ...(type === 'frame' ? { fill: 'transparent', stroke: this.color, strokeWidth: 3 } : {}) });
    this.canvas.add(shape); this.canvas.setActiveObject(shape); this.updateSelection(); this.canvas.requestRenderAll();
  }
  async importImage(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0]; input.value = '';
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'].includes(file.type) || file.size > 10_000_000) {
      this.setError('errors.invalidImage'); return;
    }
    try {
    const url = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(new Error('Cannot read image')); reader.readAsDataURL(file);
    });
    if (file.type === 'image/svg+xml') {
      const svg = atob(url.split(',')[1]);
      // Reject active/external resources; SVGs are rasterized before being stored in designs.
      if (/<!doctype|<!entity|<script|<foreignObject|<iframe|<object|<embed|\bon\w+\s*=|(?:href|xlink:href)\s*=\s*['"]\s*(?!#)|url\(\s*['"]?(?!#)/i.test(svg)) {
        this.setError('errors.unsafeSvg'); return;
      }
    }
      const image = await new Promise<FabricTypes.Image>((resolve, reject) => {
        fabric.Image.fromURL(url, img => img ? resolve(img) : reject(new Error('Image failed to load')));
      });
      const scale = Math.min(1, 200 / (image.width || 200), (this.widthMm * 8) / (image.height || 100));
      image.set({ left: 20, top: 10, scaleX: scale, scaleY: scale });
      this.canvas.add(image); this.canvas.setActiveObject(image); this.updateSelection(); this.canvas.requestRenderAll();
    } catch { this.setError('errors.decodeImage'); }
  }
  editSelected(field: string, value: string | number | boolean): void {
    if (!this.selected) return;
    this.selected.set(field as keyof FabricTypes.Object, value as never);
    this.selected.setCoords(); this.canvas.requestRenderAll();
  }
  rotate(): void { if (this.selected) this.editSelected('angle', ((this.selected.angle || 0) + 90) % 360); }
  reorder(direction: 'forward' | 'backward'): void {
    if (!this.selected) return;
    direction === 'forward' ? this.canvas.bringForward(this.selected) : this.canvas.sendBackwards(this.selected);
    this.canvas.requestRenderAll();
  }
  @HostListener('document:keydown', ['$event'])
  onKeyDown(event: KeyboardEvent): void {
    if (event.defaultPrevented || event.repeat || this.dialog.openDialogs.length) return;
    const target = event.target;
    if (target instanceof HTMLElement && (target.isContentEditable || target.closest('input, textarea, select, [contenteditable], [role="textbox"]'))) return;
    if (this.canvas?.getActiveObjects().some(object => (object as FabricTypes.Textbox).isEditing)) return;

    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey) {
      if (key === 'c' && this.canvas.getActiveObjects().length && !window.getSelection()?.toString()) {
        event.preventDefault();
        this.copySelected();
      } else if (key === 'v' && this.hasCopy) {
        event.preventDefault();
        void this.pasteCopy();
      }
    } else if (!event.ctrlKey && !event.metaKey && !event.altKey && (event.key === 'Delete' || event.key === 'Backspace') && (this.selectedMarker !== null || this.canvas.getActiveObjects().length)) {
      event.preventDefault();
      if (this.selectedMarker !== null) this.removeCutMarker(this.selectedMarker);
      else this.remove();
    }
  }

  private cloneObjects(objects: FabricTypes.Object[]): Promise<FabricTypes.Object[]> {
    return Promise.all(objects.map(object => new Promise<FabricTypes.Object>(resolve => {
      object.clone((clone: FabricTypes.Object) => resolve(clone));
    })));
  }

  private selectedSnapshot(): Promise<FabricTypes.Object[]> {
    const active = this.canvas.getActiveObject();
    if (!active) return Promise.resolve([]);
    if (active.type === 'activeSelection') {
      // Children of an active selection have group-relative coordinates. Clone
      // the selection first, then restore its children's canvas coordinates.
      return new Promise(resolve => active.clone((group: FabricTypes.ActiveSelection) => {
        const objects = group.getObjects().slice();
        group.destroy();
        resolve(objects);
      }));
    }
    return this.cloneObjects([active]);
  }

  copySelected(): void {
    if (!this.canvas.getActiveObject()) return;
    // Keep an independent snapshot: copying survives deleting the original or
    // opening another label. Paste waits for image clones to finish loading.
    this.copyPromise = this.selectedSnapshot();
    this.hasCopy = true;
    this.pasteCount = 0;
  }

  async pasteCopy(): Promise<void> {
    if (!this.copyPromise) return;
    try {
      const copies = await this.cloneObjects(await this.copyPromise);
      this.pasteCount += 1;
      this.addCopies(copies, this.pasteCount * 10);
    } catch { this.setError('errors.clipboard'); this.changeDetector.detectChanges(); }
  }

  async cloneSelected(): Promise<void> {
    if (!this.canvas.getActiveObject()) return;
    try {
      this.addCopies(await this.selectedSnapshot(), 10);
    } catch { this.setError('errors.clipboard'); this.changeDetector.detectChanges(); }
  }

  private addCopies(copies: FabricTypes.Object[], offset: number): void {
    if (this.destroyed || !copies.length) return;
    this.canvas.discardActiveObject();
    for (const copy of copies) {
      copy.set({ left: (copy.left || 0) + offset, top: (copy.top || 0) + offset });
      this.canvas.add(copy);
      copy.setCoords();
    }
    this.canvas.setActiveObject(copies.length === 1 ? copies[0] : new fabric.ActiveSelection(copies, { canvas: this.canvas }));
    this.canvas.requestRenderAll();
    this.syncSelection();
  }

  remove(): void {
    const objects = this.canvas.getActiveObjects();
    if (!objects.length) return;
    this.canvas.discardActiveObject();
    for (const object of objects) this.canvas.remove(object);
    this.canvas.requestRenderAll();
    this.syncSelection();
  }
  setBackground(): void { this.canvas.backgroundColor = this.background; this.canvas.requestRenderAll(); }
  setGradient(): void {
    this.canvas.setBackgroundColor(new fabric.Gradient({ type: 'linear', coords: { x1: 0, y1: 0, x2: this.lengthMm * UNITS_PER_MM, y2: 0 }, colorStops: [
      { offset: 0, color: this.background }, { offset: 1, color: this.color }
    ] }), () => this.canvas.requestRenderAll());
  }
  newLabel(): void {
    this.id = null; this.name = this.language.translate('design.untitled'); this.canvas.clear(); this.background = '#ffffff';
    this.cutPositionsMm = []; this.selectedMarker = null;
    this.canvas.backgroundColor = this.background; this.canvas.requestRenderAll(); this.updateSelection();
  }
  private design(): string {
    // Canvas JSON stores objects in logical units, independent of preview zoom.
    return JSON.stringify(this.canvas.toJSON());
  }
  async save(copy = false): Promise<void> {
    try {
      const id = copy || !this.id ? crypto.randomUUID() : this.id;
      const label: SavedLabel = { id, name: this.name.trim() || this.language.translate('design.untitled'), widthMm: this.widthMm,
        lengthMm: this.lengthMm, cutPositionsMm: [...this.cutPositionsMm], modified: Date.now(), design: this.design(),
        preview: this.canvas.toDataURL({ format: 'png', multiplier: 0.5 }) };
      await saveLabel(label); this.id = id; await this.refreshLibrary(); this.setStatus('printer.saved');
    } catch { this.setError('errors.save'); }
    finally { this.changeDetector.detectChanges(); }
  }
  async refreshLibrary(): Promise<void> {
    this.labels = await listLabels();
    this.changeDetector.detectChanges();
  }
  async open(label: SavedLabel): Promise<void> {
    this.id = label.id; this.name = label.name; this.widthMm = label.widthMm; this.lengthMm = label.lengthMm;
    this.cutPositionsMm = [...(label.cutPositionsMm || [])]; this.selectedMarker = null;
    this.normalizeCutPositions(); this.resize();
    await new Promise<void>(resolve => this.canvas.loadFromJSON(label.design, () => { this.canvas.renderAll(); resolve(); }));
    this.syncSelection();
  }
  async delete(label: SavedLabel): Promise<void> {
    if (!confirm(this.language.translate('library.deleteConfirm', { name: label.name }))) return;
    await deleteLabel(label.id); if (this.id === label.id) this.newLabel(); await this.refreshLibrary();
  }
  toggleCard(id: string, checked: boolean): void {
    checked ? this.selectedIds.add(id) : this.selectedIds.delete(id);
  }
  async deleteSelected(): Promise<void> {
    if (!this.selectedIds.size || !confirm(this.language.translate('library.batchConfirm', { count: this.selectedIds.size }))) return;
    for (const id of this.selectedIds) await deleteLabel(id);
    if (this.id && this.selectedIds.has(this.id)) this.newLabel();
    this.selectedIds.clear(); await this.refreshLibrary();
  }
  reorderCards(event: CdkDragDrop<SavedLabel[]>): void { moveItemInArray(this.labels, event.previousIndex, event.currentIndex); }
  private rasterDataURL(): string {
    const multiplier = DPI / (25.4 * UNITS_PER_MM * this.canvas.getZoom());
    const pixelWidth = Math.round(this.lengthMm * DPI / 25.4);
    const pixelHeight = Math.round(this.widthMm * DPI / 25.4);
    // Explicit crop dimensions avoid preview-canvas rounding changing output pixels.
    return this.canvas.toDataURL({ format: 'png', multiplier,
      width: pixelWidth / multiplier + 0.000001, height: pixelHeight / multiplier + 0.000001 });
  }
  exportPng(): void {
    const data = this.rasterDataURL();
    const a = document.createElement('a'); a.href = data; a.download = `${this.name.replace(/[^\w-]/g, '_')}.png`; a.click();
  }
  async refreshPrinters(): Promise<void> {
    try {
      const response = await fetch('/api/printers');
      if (response.ok) this.printers = (await response.json()).printers;
    } catch { this.setStatus('printer.unavailable'); }
    finally { this.changeDetector.detectChanges(); }
  }
  private static storedSubnet(): string {
    try { return localStorage.getItem(SUBNET_KEY) ?? ''; } catch { return ''; }
  }
  async discover(): Promise<void> {
    if (this.scanning) return;
    this.scanning = true; this.setError(''); this.changeDetector.detectChanges();
    const subnet = this.scanSubnet.trim();
    try { if (subnet) localStorage.setItem(SUBNET_KEY, subnet); else localStorage.removeItem(SUBNET_KEY); } catch { /* optional */ }
    try {
      const response = await fetch('/api/printers/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subnets: subnet ? subnet.split(/[\s,]+/).filter(Boolean) : [] }) });
      const result = await response.json();
      if (!response.ok) { this.setError('printer.invalidSubnet', { detail: result.detail || '' }); return; }
      this.printers = result.printers;
      if (result.hint === 'set_subnets') this.setStatus('printer.setSubnets');
      else if (!result.printers.some((item: { verified?: boolean }) => item.verified))
        this.setStatus('printer.noneFound', { subnets: result.subnets.join(', ') });
      else this.setStatus('printer.found', { count: result.printers.filter((item: { verified?: boolean }) => item.verified).length,
        subnets: result.subnets.join(', ') });
      if (!this.printer) this.printer = result.printers.find((item: { verified?: boolean }) => item.verified)?.ip ?? '';
    } catch { this.setStatus('printer.unavailable'); }
    finally { this.scanning = false; this.changeDetector.detectChanges(); }
  }
  async connect(): Promise<void> {
    try {
      const response = await fetch('/api/printers/connect', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: this.manualAddress }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Connection failed');
      this.printer = result.ip; await this.refreshPrinters();
      if (!this.canvas.getObjects().length && result.tape_width_mm) {
        this.widthMm = result.tape_width_mm; this.dimensionsChanged();
      }
      this.setStatus('printer.connected', { ...this.printerParams(result), ip: result.ip, cassette: result.cassette_type || '?' });
    } catch { this.setError('errors.connect'); }
    finally { this.changeDetector.detectChanges(); }
  }
  async checkStatus(): Promise<void> {
    if (!this.printer) return;
    try {
      const response = await fetch(`/api/printers/${encodeURIComponent(this.printer)}/status`);
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Printer unavailable');
      if (this.printStage === 'accepted' && result.status === 'IDLE') {
        this.printStage = 'complete';
        this.setStatus('printer.finished');
      } else {
        this.setStatus('printer.currentStatus', { ...this.printerParams(result), remaining: result.tape_remaining_mm ?? '?' });
      }
    } catch { this.setError('errors.status'); }
    finally { this.changeDetector.detectChanges(); }
  }
  private async watchForIdle(printer: string): Promise<void> {
    // The output slot may hold a printed label after our 40-second job check.
    // Keep checking in the background without holding the progress bar below 100%.
    const deadline = Date.now() + 120_000;
    while (!this.destroyed && this.printStage === 'accepted' && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 3000));
      if (this.destroyed || this.printStage !== 'accepted') return;
      try {
        const response = await fetch(`/api/printers/${encodeURIComponent(printer)}/status`);
        if (!response.ok) continue;
        const state = await response.json();
        if (state.status === 'IDLE' && this.printStage === 'accepted') {
          this.printStage = 'complete';
          this.setStatus('printer.finished');
          this.changeDetector.detectChanges();
          return;
        }
      } catch { /* Transient status failures do not undo a successful image ACK. */ }
    }
  }
  async print(): Promise<void> {
    if (!this.printer || this.busy) return;
    if (this.cutPositionsMm.length && this.cut === 'none') { this.setError('errors.cutMode'); return; }
    const data: PrintConfirmation = {
      printer: this.printer, lengthMm: this.lengthMm, widthMm: this.widthMm,
      cut: this.cut, segments: this.cutPositionsMm.length + 1
    };
    const approved = await firstValueFrom(this.dialog.open<boolean, PrintConfirmation>(
      PrintConfirmDialogComponent, { data, ariaLabelledBy: 'confirm-print-title',
        autoFocus: 'first-tabbable', restoreFocus: true }
    ).closed);
    if (!approved) return;
    this.busy = true; this.setError(''); this.progress = 0; this.printStage = 'queued';
    this.jobSegments = data.segments; this.jobSent = 0;
    this.changeDetector.detectChanges();
    try {
      const png = this.rasterDataURL().split(',')[1];
      const response = await fetch(`/api/printers/${encodeURIComponent(this.printer)}/print`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image: png, tape_cut: this.cut, width_mm: this.widthMm, length_mm: this.lengthMm,
          cut_positions_mm: this.cutPositionsMm }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Print failed');
      // Jobs finish only after the printer reports IDLE following the cut cycle.
      // Each segment may take up to 120s for acknowledgment, 40s to settle,
      // then 180s waiting for the previous label to be removed.
      const deadline = Date.now() + Math.max(180_000, data.segments * 360_000);
      while (Date.now() < deadline) {
        const update = await fetch(`/api/jobs/${encodeURIComponent(result.id)}`);
        if (!update.ok) { this.setError('errors.printJobStatus'); return; }
        const job = await update.json();
        this.progress = job.progress; this.printStage = job.stage;
        this.jobSent = job.sent ?? 0; this.jobSegments = job.segments ?? data.segments;
        if (job.stage === 'waiting_for_removal') {
          this.setStatus('printer.removeForNext', { sent: this.jobSent, total: this.jobSegments });
        }
        this.changeDetector.detectChanges();
        if (job.stage === 'partial') {
          this.setError('errors.partial', { sent: this.jobSent, total: this.jobSegments });
          return;
        }
        if (job.stage === 'error') throw new Error(job.error || 'Printer failed');
        if (job.stage === 'complete') { this.setStatus('printer.finished'); return; }
        if (job.stage === 'accepted') {
          this.setStatus('printer.sent');
          void this.watchForIdle(this.printer);
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 750));
      }
      this.setError('printer.timeout');
    } catch (e) { this.printError(String(e)); }
    finally { this.busy = false; this.changeDetector.detectChanges(); }
  }
}
