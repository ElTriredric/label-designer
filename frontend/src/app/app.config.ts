import { ApplicationConfig, provideZoneChangeDetection } from '@angular/core';
import { registerLocaleData } from '@angular/common';
import deLocale from '@angular/common/locales/de';
import { provideRouter } from '@angular/router';

import { provideTransloco } from '@jsverse/transloco';
import { LabelTranslationLoader, preferredLanguage } from './i18n/language.service';
import { routes } from './app.routes';

registerLocaleData(deLocale);

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    provideTransloco({
      config: { availableLangs: ['en', 'de'], defaultLang: preferredLanguage(),
        fallbackLang: 'en', reRenderOnLangChange: true, prodMode: true },
      loader: LabelTranslationLoader
    })
  ]
};
