import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { ImportsApi } from '../../core/api/imports.api';
import { ApiError } from '../../core/models/api-error.model';
import { ImportJob } from '../../core/models/domain.model';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';

/**
 * Archive sizes read the same way in the upload panel and in the history grid, so the
 * formatting lives with the screen that introduced it.
 */
export function formatArchiveSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return '—';
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const kilobytes = bytes / 1024;
  if (kilobytes < 1024) {
    return `${kilobytes.toFixed(1)} KB`;
  }
  return `${(kilobytes / 1024).toFixed(1)} MB`;
}

/**
 * Loading the onboarding archive for a new customer.
 *
 * The platform team receives one archive per customer at onboarding and loads it here
 * rather than through the document intake, because the archive carries a whole account's
 * back catalogue at once and has to be unpacked as a single unit.
 */
@Component({
  selector: 'mrd-archive-upload',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    EmptyStateComponent,
    ErrorBannerComponent,
    PageHeaderComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Load an archive"
        subtitle="Bulk onboarding archives prepared by the platform team"
      >
        <a actions class="btn btn--sm" routerLink="/imports/history">Archive history</a>
      </mrd-page-header>

      <mrd-error-banner
        [error]="failure()"
        heading="The archive was not accepted"
        [retryable]="false"
        (dismiss)="failure.set(null)"
      />

      <div class="grid grid--2">
        <section class="card">
          <div class="card__header"><h2>Archive</h2></div>
          <div class="card__body">
            <p class="muted small">
              One folder per account reference — CW-40118, CW-40233 and so on — with a
              <span class="mono">manifest.csv</span> at the root listing the folders in load
              order. Every CSV inside must be UTF-8 with the header row kept, and the invoice
              and consignment files must use the same column names as the quarter-end extract.
            </p>

            <form (submit)="submit($event)" novalidate>
              <div class="field">
                <label for="archive">Archive file</label>
                <input
                  id="archive"
                  type="file"
                  accept=".zip,.tar.gz"
                  (change)="pick($any($event.target).files)"
                />
                <p class="field__hint">Accepted formats: .zip and .tar.gz.</p>
              </div>

              @if (file(); as chosen) {
                <p class="chosen small">
                  <strong class="mono">{{ chosen.name }}</strong>
                  <span class="muted">{{ size() }}</span>
                </p>
              }

              <button type="submit" class="btn btn--primary" [disabled]="file() === null || busy()">
                {{ busy() ? 'Uploading…' : 'Upload archive' }}
              </button>
            </form>
          </div>
        </section>

        <div class="stack">
          <section class="card">
            <div class="card__header"><h2>Before you start</h2></div>
            <div class="card__body">
              <p class="muted small">
                An archive is read in full before anything is written, so a large onboarding
                pack can sit in the queue for several minutes. Leave it to finish — uploading
                the same archive twice creates a second batch and the account is loaded twice.
                Progress is on the history screen.
              </p>
            </div>
          </section>

          @if (busy()) {
            <section class="card card--pad">
              <mrd-skeleton [rows]="5" />
              <p class="muted small">Reading the archive…</p>
            </section>
          } @else {
            @if (result(); as job) {
              <section class="card">
                <div class="card__header">
                  <h2>Accepted</h2>
                  <mrd-status-pill [status]="job.state" />
                </div>
                <div class="card__body">
                  <dl class="detail">
                    <dt>Batch</dt>
                    <dd class="mono">{{ job.id }}</dd>
                    <dt>Archive</dt>
                    <dd class="mono">{{ job.archive }}</dd>
                    <dt>Entries</dt>
                    <dd>{{ job.entries | number }}</dd>
                    <dt>Size</dt>
                    <dd>{{ archiveSize(job.sizeBytes) }}</dd>
                    <dt>Uploaded by</dt>
                    <dd>{{ job.uploadedBy }}</dd>
                    <dt>Uploaded at</dt>
                    <dd>{{ job.uploadedAt | date: 'd MMM y, HH:mm' }}</dd>
                    <dt>Message</dt>
                    <dd>{{ job.message || 'Queued for unpacking.' }}</dd>
                  </dl>
                </div>
                <div class="card__footer">
                  <a class="btn btn--sm" routerLink="/imports/history">Follow it in the history</a>
                </div>
              </section>
            } @else {
              <section class="card">
                <mrd-empty-state
                  title="No batch yet"
                  message="Pick an onboarding archive and upload it; the batch it creates appears here."
                />
              </section>
            }
          }
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .card--pad {
        padding: 16px;
      }

      .card--pad p {
        margin: 8px 0 0;
      }

      .chosen {
        display: flex;
        align-items: baseline;
        gap: 10px;
        margin: 0 0 14px;
      }

      input[type='file'] {
        padding: 6px 0;
      }
    `,
  ],
})
export class ArchiveUploadComponent {
  private readonly api = inject(ImportsApi);

  readonly file = signal<File | null>(null);
  readonly busy = signal(false);
  readonly result = signal<ImportJob | null>(null);
  readonly failure = signal<ApiError | null>(null);

  readonly size = computed(() => {
    const chosen = this.file();
    return chosen === null ? '' : formatArchiveSize(chosen.size);
  });

  pick(files: FileList | null): void {
    this.file.set(files && files.length > 0 ? files[0] : null);
    this.result.set(null);
    this.failure.set(null);
  }

  submit(event: Event): void {
    event.preventDefault();

    const chosen = this.file();
    if (chosen === null || this.busy()) {
      return;
    }

    this.busy.set(true);
    this.failure.set(null);
    this.api.uploadArchive(chosen).subscribe({
      next: (job) => {
        this.busy.set(false);
        this.result.set(job);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  archiveSize(bytes: number): string {
    return formatArchiveSize(bytes);
  }
}
