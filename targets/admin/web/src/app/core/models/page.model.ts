/** Envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  page: number;
  size: number;
  total: number;
}

export const EMPTY_PAGE: Page<never> = { items: [], page: 1, size: 25, total: 0 };

/** Number of pages for a page envelope, at least one so the pager always renders. */
export function pageCount(page: Pick<Page<unknown>, 'size' | 'total'>): number {
  if (page.size <= 0) {
    return 1;
  }
  return Math.max(1, Math.ceil(page.total / page.size));
}
