import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { TariffsApi } from '../../core/api/tariffs.api';
import { ApiError } from '../../core/models/api-error.model';
import { TariffBand } from '../../core/models/domain.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  TableColumn,
} from '../../shared';

/** Look up the bands published under one code, before quoting against them. */
@Component({
  selector: 'mrd-tariff-bands',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    RouterLink,
    DataTableComponent,
    ErrorBannerComponent,
    FieldErrorComponent,
    PageHeaderComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Band lookup"
        subtitle="Find the rates published under a band code"
      >
        <a actions class="btn btn--sm" routerLink="/tariffs">Full rate card</a>
      </mrd-page-header>

      <mrd-error-banner
        [error]="failure()"
        heading="The lookup did not complete"
        (retry)="submit()"
        (dismiss)="failure.set(null)"
      />

      <div class="card lookup">
        <div class="card__header">
          <h2>Band code</h2>
          <span class="small muted">Mode, region and service level, as printed on the card</span>
        </div>
        <div class="card__body">
          <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
            <div class="row row--wrap lookup__row">
              <div class="field lookup__field">
                <label for="band-code">Band</label>
                <input id="band-code" type="search" formControlName="band" placeholder="SEA-EU-STD" />
                <mrd-field-error
                  [control]="form.controls.band"
                  label="Band"
                  [submitted]="submitted()"
                />
                <p class="field__hint">
                  Sea, air, road and rail bands follow the same shape, for example
                  <span class="mono">AIR-NORD-EXP</span> for the Nordic express service.
                </p>
              </div>
              <button type="submit" class="btn btn--primary" [disabled]="busy()">
                {{ busy() ? 'Looking up…' : 'Look up' }}
              </button>
            </div>
          </form>
        </div>
      </div>

      @if (searched(); as code) {
        <div class="card results">
          <div class="card__header">
            <h2>Bands matching <span class="mono">{{ code }}</span></h2>
            <span class="small muted">{{ results().length }} published</span>
          </div>
          <mrd-data-table
            [columns]="columns"
            [rows]="results()"
            [loading]="busy()"
            emptyTitle="No band under that code"
            emptyMessage="Nothing is published under this band. Check the code on the rate card, or ask the commercial desk whether the season has been loaded."
          >
            <a emptyAction class="btn btn--sm" routerLink="/tariffs">Browse the rate card</a>
          </mrd-data-table>
          <div class="card__footer small muted">
            Rates are exclusive of duty and of any customs handling raised at the border.
          </div>
        </div>
      }
    </div>
  `,
  styles: [
    `
      .lookup {
        margin-bottom: 16px;
      }

      .lookup__row {
        align-items: flex-end;
      }

      .lookup__field {
        min-width: 260px;
        margin-bottom: 0;
      }
    `,
  ],
})
export class TariffBandsComponent {
  private readonly api = inject(TariffsApi);
  private readonly fb = inject(FormBuilder);

  readonly form = this.fb.nonNullable.group({
    band: ['', [Validators.required, Validators.minLength(3)]],
  });

  readonly results = signal<TariffBand[]>([]);
  /** The code the results on screen belong to; null until the first lookup. */
  readonly searched = signal<string | null>(null);
  readonly busy = signal(false);
  readonly submitted = signal(false);
  readonly failure = signal<ApiError | null>(null);

  readonly columns: TableColumn<TariffBand>[] = [
    { key: 'band', label: 'Band', mono: true, width: '150px' },
    { key: 'description', label: 'Description' },
    { key: 'mode', label: 'Mode', width: '90px' },
    {
      key: 'weight',
      label: 'Weight range',
      width: '170px',
      value: (row) =>
        `${row.minWeightKg.toLocaleString('en-GB')}–${row.maxWeightKg.toLocaleString('en-GB')} kg`,
    },
    {
      key: 'ratePerKg',
      label: 'Rate per kg',
      align: 'end',
      width: '140px',
      value: (row) => `${row.ratePerKg.toFixed(2)} ${row.currency}`,
    },
    {
      key: 'minimumCharge',
      label: 'Minimum charge',
      align: 'end',
      width: '160px',
      value: (row) => `${row.minimumCharge.toFixed(2)} ${row.currency}`,
    },
    { key: 'validFrom', label: 'Valid from', width: '120px' },
    {
      key: 'validTo',
      label: 'Valid to',
      width: '120px',
      value: (row) => row.validTo ?? 'Open-ended',
    },
  ];

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);

    if (this.form.invalid || this.busy()) {
      return;
    }

    const band = this.form.getRawValue().band.trim();
    this.busy.set(true);
    this.searched.set(band);
    this.api.lookup(band).subscribe({
      next: (bands) => {
        this.busy.set(false);
        this.results.set(bands);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.results.set([]);
        this.failure.set(toApiError(error));
      },
    });
  }
}
