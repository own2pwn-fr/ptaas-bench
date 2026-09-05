import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { SearchApi } from '../../core/api/search.api';
import { ApiError } from '../../core/models/api-error.model';
import { SearchHit } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  FilterBarComponent,
  PageHeaderComponent,
  PaginationComponent,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';

/** Orders the sort control offers. Operators also share URLs with a sort already set. */
const SORT_CHOICES = [
  { value: 'updatedAt desc', label: 'Most recently updated' },
  { value: 'updatedAt asc', label: 'Least recently updated' },
  { value: 'name asc', label: 'Name (A–Z)' },
  { value: 'reference asc', label: 'Reference (ascending)' },
];

@Component({
  selector: 'mrd-search-results',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    ReactiveFormsModule,
    RouterLink,
    EmptyStateComponent,
    ErrorBannerComponent,
    FilterBarComponent,
    PageHeaderComponent,
    PaginationComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  templateUrl: './search-results.component.html',
  styleUrl: './search-results.component.scss',
})
export class SearchResultsComponent {
  private readonly api = inject(SearchApi);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  readonly sortChoices = SORT_CHOICES;
  readonly term = new FormControl('', { nonNullable: true });

  readonly query = signal('');
  readonly sort = signal(SORT_CHOICES[0].value);
  readonly page = signal(1);
  readonly results = signal<Page<SearchHit>>(EMPTY_PAGE);
  readonly loading = signal(false);
  readonly failure = signal<ApiError | null>(null);

  readonly heading = computed(() =>
    this.query() === '' ? 'Search' : `Results for “${this.query()}”`,
  );

  /** False when the order came from a shared link rather than from the picker. */
  readonly isKnownSort = computed(() =>
    SORT_CHOICES.some((choice) => choice.value === this.sort()),
  );

  constructor() {
    this.route.queryParamMap.subscribe((params) => {
      const q = params.get('q') ?? '';
      // A sort order pasted into the address bar is kept as it was typed: colleagues
      // swap report links with an order already applied.
      const sort = params.get('sort') ?? SORT_CHOICES[0].value;
      const page = Number.parseInt(params.get('page') ?? '1', 10);

      this.query.set(q);
      this.sort.set(sort);
      this.page.set(Number.isFinite(page) && page > 0 ? page : 1);
      this.term.setValue(q, { emitEvent: false });
      this.run();
    });
  }

  run(): void {
    if (this.query().trim() === '') {
      this.results.set(EMPTY_PAGE);
      return;
    }

    this.loading.set(true);
    this.failure.set(null);
    this.api.search(this.query(), this.sort(), this.page()).subscribe({
      next: (page) => {
        this.results.set(page);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  submit(): void {
    this.navigate({ q: this.term.value.trim(), page: 1 });
  }

  changeSort(sort: string): void {
    this.navigate({ sort, page: 1 });
  }

  goToPage(page: number): void {
    this.navigate({ page });
  }

  clear(): void {
    this.term.setValue('');
    void this.router.navigate(['/search']);
  }

  private navigate(changes: Record<string, string | number>): void {
    void this.router.navigate(['/search'], {
      queryParams: { q: this.query(), sort: this.sort(), page: this.page(), ...changes },
    });
  }
}
