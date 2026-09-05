import { provideHttpClient, withFetch, withInterceptors } from '@angular/common/http';
import {
  ApplicationConfig,
  ErrorHandler,
  inject,
  provideAppInitializer,
  provideZoneChangeDetection,
} from '@angular/core';
import {
  TitleStrategy,
  provideRouter,
  withComponentInputBinding,
  withInMemoryScrolling,
  withRouterConfig,
} from '@angular/router';

import { APP_ROUTES } from './app.routes';
import { accountContextInterceptor } from './core/api/account-context.interceptor';
import { sessionExpiryInterceptor } from './core/api/session-expiry.interceptor';
import { GlobalErrorHandler } from './core/error/global-error-handler';
import { SessionService } from './core/services/session.service';
import { MeridianTitleStrategy } from './core/services/title.strategy';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(
      APP_ROUTES,
      withComponentInputBinding(),
      withInMemoryScrolling({ scrollPositionRestoration: 'top', anchorScrolling: 'enabled' }),
      withRouterConfig({ paramsInheritanceStrategy: 'always' }),
    ),
    // `withFetch` keeps uploads and the diagnostics beacon on the platform fetch stack;
    // the session itself rides on the HttpOnly cookie the API sets, so nothing here has
    // to attach credentials by hand.
    provideHttpClient(
      withFetch(),
      withInterceptors([accountContextInterceptor, sessionExpiryInterceptor]),
    ),
    { provide: TitleStrategy, useClass: MeridianTitleStrategy },
    { provide: ErrorHandler, useClass: GlobalErrorHandler },
    // Guards read the role synchronously, so the session context has to be resolved
    // before the first navigation is matched.
    provideAppInitializer(() => inject(SessionService).load()),
  ],
};
