import { HttpParams } from '@angular/common/http';

/**
 * Build query parameters from a plain object, dropping entries the operator left
 * empty so that the API sees a short, cacheable query string.
 */
export function queryParams(
  source: Record<string, string | number | boolean | null | undefined>,
): HttpParams {
  let params = new HttpParams();
  for (const [key, value] of Object.entries(source)) {
    if (value === null || value === undefined || value === '') {
      continue;
    }
    params = params.set(key, String(value));
  }
  return params;
}
