/**
 * Application entry point.
 *
 * `@angular/compiler` is imported explicitly because the notice banner compiles the
 * body of an operations notice as a template at runtime — operators embed live
 * counters in them — and the compiler would otherwise be dropped from the bundle.
 */
import '@angular/compiler';

import { bootstrapApplication } from '@angular/platform-browser';

import { AppComponent } from './app/app.component';
import { appConfig } from './app/app.config';

bootstrapApplication(AppComponent, appConfig).catch((err: unknown) => {
  // Nothing has been painted at this point, so the console cannot report the failure
  // through its own error banner.
  console.error('Meridian failed to start', err);
});
