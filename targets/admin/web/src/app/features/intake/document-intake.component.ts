import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { IntakeApi } from '../../core/api/intake.api';
import { ApiError } from '../../core/models/api-error.model';
import { IntakeReceipt } from '../../core/models/domain.model';
import {
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';
import { IntakeTabsComponent } from './intake-tabs.component';

/**
 * Shape of a status message as our EDI partners send it.
 *
 * Kept here rather than fetched: it is documentation, not data, and an operator often
 * needs it while the API is the very thing that is misbehaving.
 */
const SAMPLE_DOCUMENT = `<?xml version="1.0" encoding="UTF-8"?>
<ConsignmentStatus xmlns="urn:calderwood:freight:status:2">
  <Header>
    <MessageId>CWSTAT-2026-07-19-0043</MessageId>
    <SentAt>2026-07-19T06:12:44Z</SentAt>
    <Partner code="NORDKAP">Nordkap Shipping AS</Partner>
  </Header>
  <Consignment reference="CW-40118">
    <Account>Halvard Terminals SA</Account>
    <Route>
      <Origin locode="SEGOT">Gothenburg</Origin>
      <Destination locode="NLRTM">Rotterdam</Destination>
    </Route>
    <Carriage mode="sea">
      <Vessel imo="9421678">MV Sundsvall</Vessel>
      <Voyage>247W</Voyage>
      <Etd>2026-07-19T22:00:00Z</Etd>
      <Eta>2026-07-21T05:30:00Z</Eta>
    </Carriage>
    <Events>
      <Event code="LOAD" at="2026-07-19T05:48:00Z">Loaded at berth 12</Event>
      <Event code="DEP" at="2026-07-19T06:05:00Z">Departed Gothenburg</Event>
    </Events>
    <Lines>
      <Line number="1" packages="18" grossWeightKg="7420" volumeCbm="21.4">
        Chilled seafood, reefer at 4C
      </Line>
      <Line number="2" packages="6" grossWeightKg="1980" volumeCbm="5.2">
        Insulated packaging material
      </Line>
    </Lines>
  </Consignment>
</ConsignmentStatus>`;

/** The shortest document the parser has ever accepted is comfortably longer than this. */
const MINIMUM_LENGTH = 40;

/**
 * Manual intake of a carrier message.
 *
 * When a partner's automated feed has failed, the desk asks the carrier for the raw
 * message and an operator pastes it here so the consignment keeps moving.
 */
@Component({
  selector: 'mrd-document-intake',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    DecimalPipe,
    ReactiveFormsModule,
    RouterLink,
    ErrorBannerComponent,
    FieldErrorComponent,
    IntakeTabsComponent,
    PageHeaderComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Carrier message intake"
        subtitle="Push a booking confirmation, status message or customs response through by hand"
      >
        <a actions class="btn btn--sm" routerLink="/intake/history">Recent documents</a>
      </mrd-page-header>

      <mrd-intake-tabs />

      <mrd-error-banner
        [error]="failure()"
        heading="The document was not taken in"
        [retryable]="false"
        (dismiss)="failure.set(null)"
      />

      <div class="grid grid--2">
        <section class="card">
          <div class="card__header">
            <h2>Document</h2>
            <button type="button" class="btn btn--sm btn--ghost" (click)="loadSample()">
              Load a sample structure
            </button>
          </div>

          <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
            <div class="card__body">
              <div class="field">
                <label for="document">Carrier message</label>
                <textarea
                  id="document"
                  rows="18"
                  formControlName="document"
                  spellcheck="false"
                  placeholder="Paste the message exactly as the partner sent it, opening tag included."
                ></textarea>
                <p class="field__hint">
                  Sent to the intake endpoint as <span class="mono">application/xml</span>, byte for
                  byte — do not reformat the message before submitting it.
                </p>
                <mrd-field-error
                  [control]="form.controls.document"
                  label="Carrier message"
                  [submitted]="submitted()"
                />
              </div>
            </div>

            <div class="card__footer row">
              <span class="muted small">
                {{ characterCount() | number }} characters ·
                {{ lineCount() | number }} lines
              </span>
              <span class="spacer"></span>
              <button type="button" class="btn btn--sm btn--ghost" (click)="clear()">Clear</button>
              <button type="submit" class="btn btn--sm btn--primary" [disabled]="busy()">
                {{ busy() ? 'Submitting…' : 'Submit document' }}
              </button>
            </div>
          </form>
        </section>

        <section class="card">
          <div class="card__header"><h2>Receipt</h2></div>
          <div class="card__body">
            @if (busy()) {
              <mrd-skeleton [rows]="5" />
            } @else if (receipt()) {
              @let result = receipt()!;
              <dl class="detail">
                <dt>State</dt>
                <dd><mrd-status-pill [status]="result.state" /></dd>
                <dt>Document type</dt>
                <dd>{{ result.documentType }}</dd>
                <dt>Reference</dt>
                <dd class="mono">{{ result.id }}</dd>
                <dt>Lines read</dt>
                <dd>{{ result.lineCount | number }}</dd>
                <dt>Received at</dt>
                <dd>{{ result.receivedAt | date: 'd MMM y, HH:mm:ss' }}</dd>
              </dl>

              <h3 class="small warnings__title">Warnings</h3>
              @if (result.warnings.length === 0) {
                <p class="muted small">
                  None. The document matched the partner profile on every line.
                </p>
              } @else {
                <ul class="warnings">
                  @for (warning of result.warnings; track $index) {
                    <li class="small">{{ warning }}</li>
                  }
                </ul>
              }
            } @else {
              <p class="muted small">
                The receipt appears here once the document has been taken in: the state the parser
                settled on, how many consignment lines it read, and anything the customs desk
                should look at.
              </p>
            }
          </div>
        </section>
      </div>
    </div>
  `,
  styles: [
    `
      .warnings__title {
        margin: 16px 0 6px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--mrd-ink-faint);
      }

      .warnings {
        margin: 0;
        padding-left: 18px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
    `,
  ],
})
export class DocumentIntakeComponent {
  private readonly api = inject(IntakeApi);
  private readonly fb = inject(FormBuilder);

  readonly form = this.fb.nonNullable.group({
    document: ['', [Validators.required, Validators.minLength(MINIMUM_LENGTH)]],
  });

  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly failure = signal<ApiError | null>(null);
  readonly receipt = signal<IntakeReceipt | null>(null);

  /** Counters under the box, kept in step with the textarea rather than recomputed in a pipe. */
  readonly characterCount = signal(0);
  readonly lineCount = signal(0);

  constructor() {
    this.form.controls.document.valueChanges.subscribe((value) => {
      this.characterCount.set(value.length);
      this.lineCount.set(value.length === 0 ? 0 : value.split('\n').length);
    });
  }

  loadSample(): void {
    this.form.controls.document.setValue(SAMPLE_DOCUMENT);
  }

  clear(): void {
    this.form.reset();
    this.submitted.set(false);
    this.receipt.set(null);
    this.failure.set(null);
  }

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);

    if (this.form.invalid || this.busy()) {
      return;
    }

    this.busy.set(true);
    this.api.submitDocument(this.form.getRawValue().document).subscribe({
      next: (result) => {
        this.busy.set(false);
        this.receipt.set(result);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }
}
