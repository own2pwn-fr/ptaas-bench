import { ChangeDetectionStrategy, Component, signal } from '@angular/core';

const CONSENT_KEY = 'mrd.consent.analytics';

/**
 * Consent bar for the first-party analytics cookie.
 *
 * The beacon in /assets/metrics.js stays silent until the choice stored here is
 * "granted", so declining really does stop the measurement rather than only hiding
 * the bar.
 */
@Component({
  selector: 'mrd-cookie-consent',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (open()) {
      <aside class="consent" role="region" aria-label="Cookie preferences">
        <div class="consent__text">
          <strong>Usage measurement</strong>
          <p class="small">
            Meridian sets one first-party cookie to count page views so we can see which
            screens the operations teams actually use. Nothing is shared outside
            Calderwood Group. The cookie that keeps you signed in is set either way.
          </p>
        </div>
        <div class="consent__actions">
          <button type="button" class="btn btn--sm" (click)="decide('denied')">Decline</button>
          <button type="button" class="btn btn--sm btn--primary" (click)="decide('granted')">
            Accept
          </button>
        </div>
      </aside>
    }
  `,
  styleUrl: './cookie-consent.component.scss',
})
export class CookieConsentComponent {
  private readonly openSignal = signal(readChoice() === null);
  readonly open = this.openSignal.asReadonly();

  decide(choice: 'granted' | 'denied'): void {
    try {
      window.localStorage.setItem(CONSENT_KEY, choice);
    } catch {
      // Without storage the bar reappears next time, which is the safe default.
    }
    if (choice === 'granted') {
      window.dispatchEvent(new CustomEvent('mrd:consent-granted'));
    }
    this.openSignal.set(false);
  }
}

function readChoice(): string | null {
  try {
    return window.localStorage.getItem(CONSENT_KEY);
  } catch {
    return null;
  }
}
