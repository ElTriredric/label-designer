import { Component, inject } from '@angular/core';
import { DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';
import { TranslocoPipe } from '@jsverse/transloco';

export interface PrintConfirmation {
  printer: string;
  lengthMm: number;
  widthMm: number;
  cut: string;
  segments: number;
}

@Component({
  selector: 'app-print-confirm-dialog',
  standalone: true,
  imports: [TranslocoPipe],
  template: `
    <section class="card">
      <h2 id="confirm-print-title">{{ 'confirm.title' | transloco }}</h2>
      <p>{{ 'confirm.description' | transloco }}</p>
      @if (data.segments > 1) { <p>{{ 'confirm.batchNotice' | transloco }}</p> }
      <dl>
        <dt>{{ 'confirm.printer' | transloco }}</dt><dd>{{data.printer}}</dd>
        <dt>{{ 'confirm.label' | transloco }}</dt><dd>{{data.lengthMm}} × {{data.widthMm}} mm</dd>
        <dt>{{ 'confirm.cut' | transloco }}</dt><dd>{{ ('printer.cuts.' + data.cut) | transloco }}</dd>
        <dt>{{ 'confirm.segments' | transloco }}</dt><dd>{{data.segments}}</dd>
      </dl>
      <div class="actions">
        <button type="button" (click)="ref.close(false)">{{ 'confirm.cancel' | transloco }}</button>
        <button type="button" class="primary" (click)="ref.close(true)">{{ 'confirm.send' | transloco }}</button>
      </div>
    </section>
  `,
  styles: [`
    .card { box-sizing: border-box; width: min(440px, calc(100vw - 32px)); padding: 1.5rem;
      color: #253044; background: white; border-radius: 12px; box-shadow: 0 16px 55px #152b3c70;
      font: 14px/1.5 system-ui, sans-serif; }
    h2 { font-size: 1.25rem; margin: 0; }
    p { margin: .75rem 0 1.25rem; }
    dl { display: grid; grid-template-columns: 90px 1fr; gap: .45rem .8rem; margin: 0 0 1.5rem; }
    dt { color: #536476; } dd { margin: 0; overflow-wrap: anywhere; font-weight: 600; }
    .actions { display: flex; justify-content: flex-end; gap: .65rem; }
    button { cursor: pointer; border: 1px solid #136c82; color: #136c82; background: white;
      border-radius: 6px; padding: .6rem .8rem; font: inherit; }
    button:focus-visible { outline: 3px solid #6cc8db; outline-offset: 2px; }
    .primary { color: white; background: #136c82; }
    .primary:hover { background: #095469; }
  `]
})
export class PrintConfirmDialogComponent {
  readonly data = inject<PrintConfirmation>(DIALOG_DATA);
  readonly ref = inject<DialogRef<boolean>>(DialogRef);
}
