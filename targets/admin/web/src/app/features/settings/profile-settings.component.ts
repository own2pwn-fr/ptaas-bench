import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { WorkspaceApi } from '../../core/api/workspace.api';
import { ApiError } from '../../core/models/api-error.model';
import { Profile } from '../../core/models/domain.model';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  SkeletonComponent,
} from '../../shared';

/** Languages the console is published in, alongside the offices that asked for them. */
const LOCALES: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'en-GB', label: 'English (United Kingdom)' },
  { value: 'sv-SE', label: 'Swedish (Sweden)' },
  { value: 'nl-NL', label: 'Dutch (Netherlands)' },
  { value: 'de-DE', label: 'German (Germany)' },
];

/** Zones covering the group's offices; times on every screen are shown in the one chosen. */
const TIME_ZONES: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'Europe/Stockholm', label: 'Europe/Stockholm — Gothenburg, Stockholm' },
  { value: 'Europe/Amsterdam', label: 'Europe/Amsterdam — Rotterdam' },
  { value: 'Europe/Brussels', label: 'Europe/Brussels — Antwerp, Ostend' },
  { value: 'Europe/London', label: 'Europe/London — Felixstowe' },
];

/** The fields the API accepts on the profile record. */
const EDITABLE = [
  'displayName',
  'email',
  'phone',
  'jobTitle',
  'office',
  'locale',
  'timeZone',
  'digestOptIn',
] as const;

type EditableField = (typeof EDITABLE)[number];

/** An operator's own details: what colleagues see, and how the console addresses them. */
@Component({
  selector: 'mrd-profile-settings',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
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
        title="Your profile"
        subtitle="Contact details, language and the daily digest"
      >
        <a actions class="btn btn--sm" routerLink="/settings">Back to settings</a>
      </mrd-page-header>

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      @if (loading()) {
        <div class="card card--pad"><mrd-skeleton [rows]="8" /></div>
      } @else if (record() === null) {
        <div class="card">
          <mrd-empty-state
            title="Profile unavailable"
            message="Meridian could not read your profile. Try again, or call the service desk on extension 4120."
          />
        </div>
      } @else {
        <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
          <div class="grid grid--2">
            <section class="card">
              <div class="card__header"><h2>Who you are</h2></div>
              <div class="card__body">
                <div class="field">
                  <label for="profile-name">Display name</label>
                  <input id="profile-name" type="text" formControlName="displayName" />
                  <p class="field__hint">Shown next to your entries in the audit trail.</p>
                  <mrd-field-error
                    [control]="form.controls.displayName"
                    label="Display name"
                    [submitted]="submitted()"
                  />
                </div>

                <div class="field">
                  <label for="profile-email">Work email</label>
                  <input
                    id="profile-email"
                    type="email"
                    autocomplete="email"
                    formControlName="email"
                    placeholder="first.last@calderwood.example"
                  />
                  <mrd-field-error
                    [control]="form.controls.email"
                    label="Work email"
                    [submitted]="submitted()"
                  />
                </div>

                <div class="field">
                  <label for="profile-phone">Phone</label>
                  <input
                    id="profile-phone"
                    type="text"
                    autocomplete="tel"
                    formControlName="phone"
                    placeholder="+46 31 000 000"
                  />
                  <p class="field__hint">Used by the service desk when a consignment is held.</p>
                </div>

                <div class="field">
                  <label for="profile-title">Job title</label>
                  <input
                    id="profile-title"
                    type="text"
                    formControlName="jobTitle"
                    placeholder="Forwarding coordinator"
                  />
                </div>

                <div class="field">
                  <label for="profile-office">Office</label>
                  <input
                    id="profile-office"
                    type="text"
                    formControlName="office"
                    placeholder="Gothenburg"
                  />
                </div>
              </div>
            </section>

            <section class="card">
              <div class="card__header"><h2>How the console behaves</h2></div>
              <div class="card__body">
                <div class="field">
                  <label for="profile-locale">Language</label>
                  <select id="profile-locale" formControlName="locale">
                    @for (locale of locales; track locale.value) {
                      <option [value]="locale.value">{{ locale.label }}</option>
                    }
                  </select>
                </div>

                <div class="field">
                  <label for="profile-zone">Time zone</label>
                  <select id="profile-zone" formControlName="timeZone">
                    @for (zone of timeZones; track zone.value) {
                      <option [value]="zone.value">{{ zone.label }}</option>
                    }
                  </select>
                  <p class="field__hint">
                    Departure and arrival times are shown in this zone, whatever the port.
                  </p>
                </div>

                <div class="field">
                  <label class="digest">
                    <input type="checkbox" formControlName="digestOptIn" />
                    <span>
                      <strong>Send me the daily digest</strong>
                      <span class="muted small">
                        One email at 06:30 in your time zone listing customs holds, invoices
                        falling due and approvals still waiting on your accounts.
                      </span>
                    </span>
                  </label>
                </div>

                @if (saved()) {
                  <p class="result">Profile updated.</p>
                }
              </div>
              <div class="card__footer row">
                <button type="submit" class="btn btn--primary" [disabled]="busy() || !dirty()">
                  {{ busy() ? 'Saving…' : 'Save changes' }}
                </button>
                <button
                  type="button"
                  class="btn btn--ghost"
                  [disabled]="busy() || !dirty()"
                  (click)="discard()"
                >
                  Discard changes
                </button>
                <span class="spacer"></span>
                <span class="muted small">
                  @if (dirty()) {
                    Unsaved changes
                  } @else {
                    Everything saved
                  }
                </span>
              </div>
            </section>
          </div>
        </form>
      }
    </div>
  `,
  styles: [
    `
      .card--pad {
        padding: 16px;
      }

      .digest {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        font-weight: 400;
      }

      .digest input {
        width: auto;
        margin-top: 3px;
      }

      .digest span {
        display: flex;
        flex-direction: column;
      }

      .result {
        margin: 8px 0 0;
        padding: 10px 12px;
        background: var(--mrd-good-soft);
        border-radius: var(--mrd-radius-sm);
      }
    `,
  ],
})
export class ProfileSettingsComponent {
  private readonly api = inject(WorkspaceApi);
  private readonly fb = inject(FormBuilder);

  readonly locales = LOCALES;
  readonly timeZones = TIME_ZONES;

  readonly record = signal<Profile | null>(null);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);
  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly saved = signal(false);

  readonly form = this.fb.nonNullable.group({
    displayName: ['', [Validators.required]],
    email: ['', [Validators.required, Validators.email]],
    phone: [''],
    jobTitle: [''],
    office: [''],
    locale: ['en-GB'],
    timeZone: ['Europe/Stockholm'],
    digestOptIn: [false],
  });

  /** Mirrors the form so the footer can react without a change-detection pass. */
  private readonly formValue = signal(this.form.getRawValue());

  readonly dirty = computed(() => changedFields(this.record(), this.formValue()).length > 0);

  constructor() {
    this.load();
    this.form.valueChanges.subscribe(() => {
      this.formValue.set(this.form.getRawValue());
      this.saved.set(false);
    });
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.profile().subscribe({
      next: (profile) => {
        this.record.set(profile);
        this.fill(profile);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);
    this.saved.set(false);

    if (this.form.invalid || this.busy()) {
      return;
    }

    const loaded = this.record();
    const values = this.form.getRawValue();
    const fields = changedFields(loaded, values);
    if (fields.length === 0) {
      return;
    }

    // Send only what moved: the API treats an absent field as untouched, which keeps two
    // operators editing the same record from overwriting each other's details.
    const changes: Partial<Profile> = {};
    for (const field of fields) {
      if (field === 'digestOptIn') {
        changes.digestOptIn = values.digestOptIn;
      } else {
        changes[field] = values[field];
      }
    }

    this.busy.set(true);
    this.api.updateProfile(changes).subscribe({
      next: (profile) => {
        this.busy.set(false);
        this.submitted.set(false);
        this.saved.set(true);
        this.record.set(profile);
        this.fill(profile);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  discard(): void {
    const loaded = this.record();
    if (loaded !== null) {
      this.fill(loaded);
    }
    this.submitted.set(false);
    this.saved.set(false);
  }

  private fill(profile: Profile): void {
    this.form.reset({
      displayName: profile.displayName ?? '',
      email: profile.email ?? '',
      phone: profile.phone ?? '',
      jobTitle: profile.jobTitle ?? '',
      office: profile.office ?? '',
      locale: profile.locale || 'en-GB',
      timeZone: profile.timeZone || 'Europe/Stockholm',
      digestOptIn: profile.digestOptIn === true,
    });
    this.formValue.set(this.form.getRawValue());
    this.saved.set(false);
  }
}

/** Fields whose value differs from the record the screen loaded. */
function changedFields(
  loaded: Profile | null,
  values: Record<EditableField, string | boolean>,
): EditableField[] {
  if (loaded === null) {
    return [];
  }

  return EDITABLE.filter((field) => {
    const original = field === 'digestOptIn' ? loaded.digestOptIn === true : (loaded[field] ?? '');
    return values[field] !== original;
  });
}
