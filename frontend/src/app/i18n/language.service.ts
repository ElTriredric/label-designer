import { Injectable, inject } from '@angular/core';
import { TranslocoLoader, TranslocoService, Translation } from '@jsverse/transloco';
import { of } from 'rxjs';
import en from './en.json';
import de from './de.json';

export type Language = 'en' | 'de';
const STORAGE_KEY = 'label-designer-language';
const translations: Record<Language, Translation> = { en, de };

export function preferredLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'en' || stored === 'de') return stored;
  } catch { /* Private browsing may disable storage. */ }
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('de') ? 'de' : 'en';
}

@Injectable({ providedIn: 'root' })
export class LabelTranslationLoader implements TranslocoLoader {
  getTranslation(lang: string) {
    return of(translations[lang === 'de' ? 'de' : 'en']);
  }
}

@Injectable({ providedIn: 'root' })
export class LanguageService {
  private readonly transloco = inject(TranslocoService);
  readonly current = this.transloco.activeLang;

  constructor() {
    // Register bundled dictionaries so imperative messages work even before
    // the first Transloco pipe subscribes to a language load.
    this.transloco.setTranslation(en, 'en');
    this.transloco.setTranslation(de, 'de');
    if (typeof document !== 'undefined') this.updateDocument(this.current());
  }

  setLanguage(value: string): void {
    if (value !== 'en' && value !== 'de') return;
    this.transloco.setActiveLang(value);
    if (typeof document !== 'undefined') this.updateDocument(value);
    try { localStorage.setItem(STORAGE_KEY, value); } catch { /* Still switch for this session. */ }
  }

  private updateDocument(value: Language | string): void {
    document.documentElement.lang = value;
    document.title = this.translate('app.title');
  }

  translate(key: string, params?: Record<string, string | number>): string {
    return this.transloco.translate(key, params);
  }
}
