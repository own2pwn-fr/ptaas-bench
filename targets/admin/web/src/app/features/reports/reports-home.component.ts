import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';

import { PageHeaderComponent } from '../../shared';
import { ReportTabsComponent } from './report-tabs.component';

interface ReportChoice {
  link: string;
  title: string;
  audience: string;
  description: string;
}

/** The three reports the console publishes, in the order finance ask for them. */
const REPORT_CHOICES: ReportChoice[] = [
  {
    link: '/reports/ledger',
    title: 'Ledger',
    audience: 'Finance',
    description:
      'Postings for a period, with the opening and closing balance. Used to reconcile a ' +
      'customer statement before it is sent, and to answer queries about a disputed ' +
      'disbursement.',
  },
  {
    link: '/reports/summary',
    title: 'Account summary',
    audience: 'Account management',
    description:
      'The headline figures for one account: consignments in flight, invoiced value, ' +
      'customs holds and the busiest lanes. This is what the desk reads out on a ' +
      'quarterly account review.',
  },
  {
    link: '/reports/volumes',
    title: 'Volumes',
    audience: 'Operations planning',
    description:
      'Consignments, TEU and chargeable weight per day, week or month. Operations use it ' +
      'to plan quay slots and to check a lane against the capacity booked with the carrier.',
  },
];

/** Landing page for reporting: pick a report, nothing else. */
@Component({
  selector: 'mrd-reports-home',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, PageHeaderComponent, ReportTabsComponent],
  template: `
    <div class="page">
      <mrd-page-header
        title="Reports"
        subtitle="Ledger, account summary and volumes for the Calderwood network"
      />

      <mrd-report-tabs />

      <div class="grid grid--3">
        @for (choice of choices; track choice.link) {
          <a class="card choice" [routerLink]="choice.link">
            <span class="choice__audience small muted">{{ choice.audience }}</span>
            <strong class="choice__title">{{ choice.title }}</strong>
            <span class="choice__description small">{{ choice.description }}</span>
            <span class="choice__go small">Open report →</span>
          </a>
        }
      </div>

      <p class="muted small footnote">
        Figures are compiled from postings settled up to the previous working day. A period
        that spans quarter end may move once the customs desk closes its adjustments.
      </p>
    </div>
  `,
  styles: [
    `
      .choice {
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding: 16px;
        color: inherit;
      }

      .choice:hover {
        text-decoration: none;
        border-color: var(--mrd-line-strong);
      }

      .choice__audience {
        text-transform: uppercase;
        letter-spacing: 0.05em;
      }

      .choice__title {
        font-size: 16px;
      }

      .choice__description {
        color: var(--mrd-ink-soft);
        line-height: 1.5;
      }

      .choice__go {
        margin-top: auto;
        padding-top: 8px;
        color: var(--mrd-accent);
        font-weight: 600;
      }

      .footnote {
        margin-top: 20px;
      }
    `,
  ],
})
export class ReportsHomeComponent {
  readonly choices = REPORT_CHOICES;
}
