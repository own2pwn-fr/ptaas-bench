import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { debounceTime } from 'rxjs';

import { toApiError } from '../../core/api/api-error.util';
import { DirectoryApi } from '../../core/api/directory.api';
import { ApiError } from '../../core/models/api-error.model';
import { Person } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FilterBarComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';

/** Departments as they appear on the group organisation chart. */
const DEPARTMENTS = [
  'Operations',
  'Customs',
  'Finance',
  'Commercial',
  'Platform',
  'HR',
  'Chartering',
];

/** Everyone in the group, with the extension the desk needs to reach them. */
@Component({
  selector: 'mrd-people-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    DataTableComponent,
    ErrorBannerComponent,
    FilterBarComponent,
    PageHeaderComponent,
    PaginationComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Directory"
        subtitle="Colleagues across the Calderwood offices, their desks and extensions"
      />

      <mrd-error-banner
        [error]="failure()"
        heading="The directory did not answer"
        (retry)="load()"
        (dismiss)="failure.set(null)"
      />

      <mrd-filter-bar (reset)="clear()">
        <div class="field">
          <label for="people-surname">Surname</label>
          <input
            id="people-surname"
            type="search"
            [formControl]="surname"
            placeholder="Lindqvist, Okonkwo, Devriendt…"
          />
          <p class="field__hint">Starts typing; results follow after a short pause.</p>
        </div>

        <div class="field">
          <label for="people-department">Department</label>
          <select id="people-department" [formControl]="department">
            <option value="">All departments</option>
            @for (name of departments; track name) {
              <option [value]="name">{{ name }}</option>
            }
          </select>
        </div>
      </mrd-filter-bar>

      <div class="card">
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          [link]="personLink"
          [trackBy]="'uid'"
          emptyTitle="Nobody matches"
          emptyMessage="No colleague matches that surname in the selected department. Try a shorter surname, or clear the department."
        />
        <mrd-pagination
          [page]="page().page"
          [size]="page().size"
          [total]="page().total"
          (pageChange)="goToPage($event)"
        />
      </div>
    </div>
  `,
})
export class PeopleListComponent {
  private readonly api = inject(DirectoryApi);

  readonly departments = DEPARTMENTS;
  readonly surname = new FormControl('', { nonNullable: true });
  readonly department = new FormControl('', { nonNullable: true });

  readonly page = signal<Page<Person>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly columns: TableColumn<Person>[] = [
    { key: 'displayName', label: 'Name', width: '200px' },
    { key: 'jobTitle', label: 'Job title' },
    { key: 'department', label: 'Department', width: '140px' },
    { key: 'office', label: 'Office', width: '140px' },
    { key: 'extension', label: 'Extension', mono: true, width: '110px' },
    { key: 'email', label: 'Email', width: '240px' },
  ];

  constructor() {
    // A surname is typed a letter at a time; waiting for a pause keeps the directory
    // service from answering a request per keystroke.
    this.surname.valueChanges.pipe(debounceTime(300)).subscribe(() => {
      this.resetToFirstPage();
      this.load();
    });

    this.department.valueChanges.subscribe(() => {
      this.resetToFirstPage();
      this.load();
    });

    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api
      .people({
        surname: this.surname.value.trim(),
        department: this.department.value,
        page: this.page().page,
      })
      .subscribe({
        next: (result) => {
          this.page.set(result);
          this.loading.set(false);
        },
        error: (error: unknown) => {
          this.loading.set(false);
          this.failure.set(toApiError(error));
        },
      });
  }

  goToPage(page: number): void {
    this.page.update((current) => ({ ...current, page }));
    this.load();
  }

  clear(): void {
    this.surname.setValue('', { emitEvent: false });
    this.department.setValue('', { emitEvent: false });
    this.resetToFirstPage();
    this.load();
  }

  private resetToFirstPage(): void {
    this.page.update((current) => ({ ...current, page: 1 }));
  }

  personLink = (row: Person): unknown[] => ['/directory', row.uid];
}
