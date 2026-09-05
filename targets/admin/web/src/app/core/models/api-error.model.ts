/**
 * The single error shape the whole console renders.
 *
 * Every service funnels failures through `toApiError`, so a feature only ever has to
 * hold one `ApiError | null` signal and hand it to `<mrd-error-banner>`.
 */
export interface ApiError {
  /** HTTP status, or 0 when the request never reached the API. */
  status: number;
  /** Short machine-readable code from the API when it sends one. */
  code: string;
  /** Message safe to show to an operator. */
  message: string;
  /** Per-field messages for form submissions. */
  fieldErrors?: Record<string, string>;
  /** Correlation id echoed by the API gateway; the service desk asks for it. */
  reference?: string;
}
