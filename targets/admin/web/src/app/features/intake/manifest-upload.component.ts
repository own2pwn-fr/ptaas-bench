import { DatePipe, DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  inject,
  signal,
  viewChild,
} from '@angular/core';
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

/** What the intake service accepts on the manifest endpoint, in the order the desk sees them. */
const ACCEPTED_FORMATS = [
  { extension: '.csv', note: 'Terminal line listings, one consignment line per row' },
  { extension: '.xml', note: 'Partner manifests in the urn:calderwood:freight namespace' },
  { extension: '.edi', note: 'UN/EDIFACT IFTMIN and IFTSTA interchanges' },
  { extension: '.txt', note: 'Fixed-width listings from the older port systems' },
];

/** Matches the limit the gateway enforces; rejecting here saves a long upload. */
const SIZE_LIMIT_BYTES = 25 * 1024 * 1024;

/**
 * Manifest upload.
 *
 * The file goes up as multipart under the `manifest` field, which is what the intake
 * service expects and what the terminal operators are used to sending by SFTP.
 */
@Component({
  selector: 'mrd-manifest-upload',
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
        title="Manifest upload"
        subtitle="Send a terminal or partner manifest straight to the intake service"
      >
        <a actions class="btn btn--sm" routerLink="/intake/history">Recent documents</a>
      </mrd-page-header>

      <mrd-intake-tabs />

      <mrd-error-banner
        [error]="failure()"
        heading="The manifest was not taken in"
        [retryable]="false"
        (dismiss)="failure.set(null)"
      />

      <div class="grid grid--2">
        <section class="card">
          <div class="card__header"><h2>File</h2></div>

          <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
            <div class="card__body">
              <div class="field">
                <label for="manifest">Manifest file</label>
                <input
                  #picker
                  id="manifest"
                  type="file"
                  accept=".csv,.xml,.edi,.txt"
                  (change)="choose($event)"
                />
                <p class="field__hint">
                  Up to {{ sizeLimitLabel }} per file. Larger interchanges stay on the SFTP drop
                  and are picked up by the overnight run.
                </p>
                <mrd-field-error
                  [control]="form.controls.manifest"
                  label="Manifest file"
                  [submitted]="submitted()"
                />
              </div>

              @if (chosen(); as file) {
                <dl class="detail">
                  <dt>Chosen file</dt>
                  <dd class="mono">{{ file.name }}</dd>
                  <dt>Size</dt>
                  <dd>{{ sizeLabel() }}</dd>
                </dl>
              }

              <h3 class="small formats__title">Accepted formats</h3>
              <ul class="formats">
                @for (format of formats; track format.extension) {
                  <li class="small">
                    <span class="mono">{{ format.extension }}</span> — {{ format.note }}
                  </li>
                }
              </ul>
            </div>

            <div class="card__footer row">
              <span class="muted small">Field name <span class="mono">manifest</span></span>
              <span class="spacer"></span>
              <button type="button" class="btn btn--sm btn--ghost" (click)="clear()">Clear</button>
              <button
                type="submit"
                class="btn btn--sm btn--primary"
                [disabled]="busy() || chosen() === null"
              >
                {{ busy() ? 'Uploading…' : 'Upload manifest' }}
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

              <h3 class="small formats__title">Warnings</h3>
              @if (result.warnings.length === 0) {
                <p class="muted small">None. Every line matched a known consignment.</p>
              } @else {
                <ul class="formats">
                  @for (warning of result.warnings; track $index) {
                    <li class="small">{{ warning }}</li>
                  }
                </ul>
              }
            } @else {
              <p class="muted small">
                Choose a manifest and upload it; the receipt with the line count and any warnings
                appears here. Nothing is filed against a consignment until the receipt says
                accepted.
              </p>
            }
          </div>
        </section>
      </div>
    </div>
  `,
  styles: [
    `
      .formats__title {
        margin: 16px 0 6px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--mrd-ink-faint);
      }

      .formats {
        margin: 0;
        padding-left: 18px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }

      input[type='file'] {
        padding: 6px 0;
      }
    `,
  ],
})
export class ManifestUploadComponent {
  private readonly api = inject(IntakeApi);
  private readonly fb = inject(FormBuilder);

  /**
   * The control holds the file name only — a File cannot live in a form value — but it
   * gives the screen the same required-field wording as every other form in the console.
   */
  readonly form = this.fb.nonNullable.group({
    manifest: ['', [Validators.required]],
  });

  /** Native file inputs keep their selection until the element itself is reset. */
  private readonly picker = viewChild<ElementRef<HTMLInputElement>>('picker');

  readonly formats = ACCEPTED_FORMATS;
  readonly sizeLimitLabel = formatBytes(SIZE_LIMIT_BYTES);

  readonly chosen = signal<File | null>(null);
  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly failure = signal<ApiError | null>(null);
  readonly receipt = signal<IntakeReceipt | null>(null);

  readonly sizeLabel = computed(() => {
    const file = this.chosen();
    return file === null ? '—' : formatBytes(file.size);
  });

  choose(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;

    this.chosen.set(file);
    this.receipt.set(null);
    this.failure.set(null);
    this.form.controls.manifest.setValue(file?.name ?? '');
    this.form.controls.manifest.markAsTouched();

    if (file !== null && file.size > SIZE_LIMIT_BYTES) {
      this.form.controls.manifest.setErrors({
        message: `The manifest is larger than the ${this.sizeLimitLabel} limit. Drop it on the SFTP folder instead.`,
      });
    }
  }

  clear(): void {
    const picker = this.picker();
    if (picker !== undefined) {
      picker.nativeElement.value = '';
    }

    this.chosen.set(null);
    this.form.reset();
    this.submitted.set(false);
    this.receipt.set(null);
    this.failure.set(null);
  }

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);

    const file = this.chosen();
    if (file === null || this.form.invalid || this.busy()) {
      return;
    }

    this.busy.set(true);
    this.api.uploadManifest(file).subscribe({
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

/** Sizes are quoted the way the terminals quote them: whole MB once past a megabyte. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} kB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(bytes % (1024 * 1024) === 0 ? 0 : 1)} MB`;
}
