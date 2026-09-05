import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

export type PillTone = 'neutral' | 'good' | 'warn' | 'bad' | 'info';

/** Known states across the console mapped to a tone, so colours stay consistent. */
const TONES: Readonly<Record<string, PillTone>> = {
  active: 'good',
  approved: 'good',
  accepted: 'good',
  delivered: 'good',
  settled: 'good',
  done: 'good',
  ok: 'good',
  sent: 'good',
  success: 'good',
  enabled: 'good',
  pending: 'warn',
  queued: 'warn',
  processing: 'warn',
  running: 'warn',
  extracting: 'warn',
  'on-hold': 'warn',
  'customs-hold': 'warn',
  'part-paid': 'warn',
  deferred: 'warn',
  invited: 'warn',
  draft: 'neutral',
  prospect: 'neutral',
  cancelled: 'neutral',
  withdrawn: 'neutral',
  closed: 'neutral',
  suppressed: 'neutral',
  disabled: 'neutral',
  never: 'neutral',
  overdue: 'bad',
  rejected: 'bad',
  failed: 'bad',
  denied: 'bad',
  error: 'bad',
  bounced: 'bad',
  suspended: 'bad',
  critical: 'bad',
  'in-transit': 'info',
  booked: 'info',
  issued: 'info',
  warning: 'warn',
  info: 'info',
};

@Component({
  selector: 'mrd-status-pill',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="pill" [class]="'pill--' + resolvedTone()">{{ label() }}</span>`,
  styles: [
    `
      .pill {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 11.5px;
        font-weight: 600;
        letter-spacing: 0.01em;
        white-space: nowrap;
        text-transform: capitalize;
      }

      .pill--neutral {
        background: var(--mrd-surface-sunken);
        color: var(--mrd-ink-soft);
      }

      .pill--good {
        background: var(--mrd-good-soft);
        color: var(--mrd-good);
      }

      .pill--warn {
        background: var(--mrd-amber-soft);
        color: var(--mrd-amber);
      }

      .pill--bad {
        background: var(--mrd-danger-soft);
        color: var(--mrd-danger);
      }

      .pill--info {
        background: var(--mrd-accent-soft);
        color: var(--mrd-accent-ink);
      }
    `,
  ],
})
export class StatusPillComponent {
  readonly status = input.required<string>();
  readonly tone = input<PillTone | null>(null);

  readonly label = computed(() => this.status().replace(/[-_]/g, ' '));
  readonly resolvedTone = computed<PillTone>(
    () => this.tone() ?? TONES[this.status().toLowerCase()] ?? 'neutral',
  );
}
