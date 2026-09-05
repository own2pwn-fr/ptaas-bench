import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { WorkspaceApi } from '../../core/api/workspace.api';
import { ApiError } from '../../core/models/api-error.model';
import { WorkspaceLayout } from '../../core/models/domain.model';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  SkeletonComponent,
} from '../../shared';

type PanelKey = 'consignments' | 'approvals' | 'customsHolds' | 'invoicesDue' | 'laneVolumes';

/** Panels the overview can show, in the order they are stacked. */
const PANELS: ReadonlyArray<{ key: PanelKey; label: string; hint: string }> = [
  { key: 'consignments', label: 'Consignments in flight', hint: 'Everything currently moving' },
  { key: 'approvals', label: 'Approvals waiting', hint: 'Credit limits and rate overrides' },
  { key: 'customsHolds', label: 'Customs holds', hint: 'Consignments stopped at a border' },
  { key: 'invoicesDue', label: 'Invoices due', hint: 'What the ledger expects this week' },
  { key: 'laneVolumes', label: 'Lane volumes', hint: 'Gothenburg, Rotterdam, Gdansk, Antwerp' },
];

interface DecodedLayout {
  /** Text shown in the payload block, pretty-printed when it parses as JSON. */
  text: string;
  isJson: boolean;
  decodable: boolean;
}

/**
 * The saved console arrangement.
 *
 * Operators move between the branch office and the terminal during a shift and expect the
 * console to look the same in both places, so the arrangement is held by the API rather
 * than by the browser they happen to be sitting at.
 */
@Component({
  selector: 'mrd-workspace-settings',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    ReactiveFormsModule,
    RouterLink,
    EmptyStateComponent,
    ErrorBannerComponent,
    FieldErrorComponent,
    PageHeaderComponent,
    SkeletonComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Console layout"
        subtitle="The arrangement that follows you between workstations"
      >
        <a actions class="btn btn--sm" routerLink="/settings">Back to settings</a>
      </mrd-page-header>

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <div class="grid grid--2">
        <section class="card">
          <div class="card__header">
            <h2>Saved arrangement</h2>
            @if (layout()?.updatedAt; as updatedAt) {
              <span class="muted small">Saved {{ updatedAt | date: 'd MMM y, HH:mm' }}</span>
            }
          </div>
          @if (loading()) {
            <div class="card__body"><mrd-skeleton [rows]="5" /></div>
          } @else if (!hasState()) {
            <mrd-empty-state
              title="Nothing saved yet"
              message="You are on the standard arrangement. Choose your panels and save to keep them."
            />
          } @else {
            <div class="card__body stack">
              @if (!decoded().decodable) {
                <p class="muted small">
                  The stored arrangement could not be read back, so it is shown exactly as it
                  is held. Saving from this screen replaces it.
                </p>
              } @else if (!decoded().isJson) {
                <p class="muted small">
                  This arrangement is not in the usual format, so it is shown as plain text.
                </p>
              }
              <pre class="payload">{{ decoded().text }}</pre>
              @if (layout()?.updatedBy; as updatedBy) {
                <p class="muted small">Last saved by {{ updatedBy }}</p>
              }
            </div>
          }
        </section>

        <section class="card">
          <div class="card__header"><h2>Panels and density</h2></div>
          <form [formGroup]="form" (ngSubmit)="save()" novalidate>
            <div class="card__body">
              <div class="stack">
                @for (panel of panels; track panel.key) {
                  <label class="panel">
                    <input type="checkbox" [formControlName]="panel.key" />
                    <span>
                      <strong>{{ panel.label }}</strong>
                      <span class="muted small">{{ panel.hint }}</span>
                    </span>
                  </label>
                }
              </div>

              <div class="field density">
                <label for="layout-density">Table density</label>
                <select id="layout-density" formControlName="density">
                  <option value="comfortable">Comfortable</option>
                  <option value="compact">Compact — more rows on a terminal screen</option>
                </select>
              </div>

              @if (saved()) {
                <p class="result">
                  Arrangement saved. It will be applied the next time you open the overview,
                  wherever you sign in.
                </p>
              }
            </div>
            <div class="card__footer row">
              <button type="submit" class="btn btn--primary" [disabled]="busy()">
                {{ busy() ? 'Saving…' : 'Save current layout' }}
              </button>
              <span class="spacer"></span>
              <span class="muted small">{{ visibleCount() }} of {{ panels.length }} panels shown</span>
            </div>
          </form>
        </section>
      </div>

      <section class="card import">
        <div class="card__header">
          <h2>Import a layout from another workstation</h2>
        </div>
        <form [formGroup]="importForm" (ngSubmit)="restore()" novalidate>
          <div class="card__body">
            <p class="muted small">
              Colleagues who have already arranged the console the way your desk works can send
              you their layout string. Paste it below and it becomes yours; nothing else about
              their account is copied.
            </p>
            <div class="field">
              <label for="layout-import">Layout string</label>
              <textarea
                id="layout-import"
                formControlName="pasted"
                spellcheck="false"
                placeholder="Paste the layout string a colleague sent you"
              ></textarea>
              <mrd-field-error
                [control]="importForm.controls.pasted"
                label="Layout string"
                [submitted]="importSubmitted()"
              />
            </div>

            @if (restored(); as record) {
              <div class="restored stack">
                <strong>Layout applied.</strong>
                <span class="muted small">
                  @if (record.updatedAt) {
                    Saved to your account at {{ record.updatedAt | date: 'd MMM y, HH:mm' }}.
                  } @else {
                    Saved to your account.
                  }
                </span>
                <pre class="payload">{{ restoredText() }}</pre>
              </div>
            }
          </div>
          <div class="card__footer row">
            <button type="submit" class="btn btn--primary" [disabled]="importBusy()">
              {{ importBusy() ? 'Applying…' : 'Apply this layout' }}
            </button>
            <button type="button" class="btn btn--ghost" (click)="clearImport()">Clear</button>
          </div>
        </form>
      </section>
    </div>
  `,
  styles: [
    `
      .panel {
        display: flex;
        align-items: flex-start;
        gap: 10px;
      }

      .panel input {
        width: auto;
        margin-top: 3px;
      }

      .panel span {
        display: flex;
        flex-direction: column;
      }

      .density {
        margin-top: 16px;
        margin-bottom: 0;
      }

      .result {
        margin: 16px 0 0;
        padding: 10px 12px;
        background: var(--mrd-good-soft);
        border-radius: var(--mrd-radius-sm);
      }

      .restored {
        margin-top: 12px;
        padding: 10px 12px;
        background: var(--mrd-good-soft);
        border-radius: var(--mrd-radius-sm);
      }

      .import {
        margin-top: 16px;
      }
    `,
  ],
})
export class WorkspaceSettingsComponent {
  private readonly api = inject(WorkspaceApi);
  private readonly fb = inject(FormBuilder);

  readonly panels = PANELS;
  readonly layout = signal<WorkspaceLayout | null>(null);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);
  readonly busy = signal(false);
  readonly saved = signal(false);
  readonly importBusy = signal(false);
  readonly importSubmitted = signal(false);
  readonly restored = signal<WorkspaceLayout | null>(null);

  readonly form = this.fb.nonNullable.group({
    consignments: [true],
    approvals: [true],
    customsHolds: [true],
    invoicesDue: [false],
    laneVolumes: [false],
    density: ['comfortable'],
  });

  readonly importForm = this.fb.nonNullable.group({
    pasted: ['', [Validators.required]],
  });

  readonly hasState = computed(() => (this.layout()?.state ?? '') !== '');
  readonly decoded = computed(() => decodeLayout(this.layout()?.state ?? ''));
  readonly restoredText = computed(() => decodeLayout(this.restored()?.state ?? '').text);
  /** Mirrors the panel form so the counter in the footer stays a computed signal. */
  private readonly formValue = signal(this.form.getRawValue());

  readonly visibleCount = computed(() => {
    const values = this.formValue();
    return PANELS.filter((panel) => values[panel.key]).length;
  });

  constructor() {
    this.load();
    this.form.valueChanges.subscribe(() => this.formValue.set(this.form.getRawValue()));
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.layout().subscribe({
      next: (layout) => {
        this.layout.set(layout);
        this.applyToControls(layout.state);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  save(): void {
    if (this.busy()) {
      return;
    }

    this.failure.set(null);
    this.saved.set(false);
    this.busy.set(true);

    const values = this.form.getRawValue();
    const arrangement = {
      version: 1,
      density: values.density,
      panels: PANELS.map((panel) => ({ name: panel.key, visible: values[panel.key] })),
    };

    this.api.saveLayout(btoa(JSON.stringify(arrangement))).subscribe({
      next: (layout) => {
        this.busy.set(false);
        this.saved.set(true);
        this.layout.set(layout);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  restore(): void {
    this.importSubmitted.set(true);
    this.failure.set(null);
    this.restored.set(null);

    if (this.importForm.invalid || this.importBusy()) {
      return;
    }

    this.importBusy.set(true);
    this.api.restoreLayout(this.importForm.getRawValue().pasted.trim()).subscribe({
      next: (layout) => {
        this.importBusy.set(false);
        this.importSubmitted.set(false);
        this.restored.set(layout);
        this.layout.set(layout);
        this.applyToControls(layout.state);
      },
      error: (error: unknown) => {
        this.importBusy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  clearImport(): void {
    this.importForm.reset({ pasted: '' });
    this.importSubmitted.set(false);
    this.restored.set(null);
  }

  /** Bring the checkboxes in line with whatever arrangement is currently held. */
  private applyToControls(state: string): void {
    const decoded = decodeLayout(state);
    if (!decoded.isJson || decoded.text === '') {
      return;
    }

    const parsed: unknown = JSON.parse(decoded.text);
    if (typeof parsed !== 'object' || parsed === null) {
      return;
    }

    const record = parsed as { density?: unknown; panels?: unknown };
    if (typeof record.density === 'string') {
      this.form.controls.density.setValue(record.density);
    }

    if (!Array.isArray(record.panels)) {
      return;
    }

    for (const entry of record.panels as Array<{ name?: unknown; visible?: unknown }>) {
      const panel = PANELS.find((known) => known.key === entry.name);
      if (panel && typeof entry.visible === 'boolean') {
        this.form.controls[panel.key].setValue(entry.visible);
      }
    }
  }
}

/** Layouts travel as base64; show the arrangement rather than the encoded string. */
function decodeLayout(state: string): DecodedLayout {
  if (state === '') {
    return { text: '', isJson: false, decodable: true };
  }

  let text: string;
  try {
    text = atob(state);
  } catch {
    return { text: state, isJson: false, decodable: false };
  }

  try {
    return { text: JSON.stringify(JSON.parse(text), null, 2), isJson: true, decodable: true };
  } catch {
    return { text, isJson: false, decodable: true };
  }
}
