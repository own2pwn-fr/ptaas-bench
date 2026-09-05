import { HttpErrorResponse } from '@angular/common/http';

import { ApiError } from '../models/api-error.model';

interface ApiErrorBody {
  code?: string;
  message?: string;
  detail?: string;
  error?: string;
  reference?: string;
  fieldErrors?: Record<string, string>;
}

const NETWORK_MESSAGE =
  'Meridian could not reach the API. Check your connection and try again.';

const STATUS_MESSAGES: Record<number, string> = {
  400: 'The request was rejected as malformed.',
  401: 'Your session has expired. Sign in again to continue.',
  403: 'You do not have permission to perform this action.',
  404: 'The requested record no longer exists.',
  409: 'Another operator changed this record while you were editing it.',
  413: 'The upload is larger than the console accepts.',
  422: 'Some of the values supplied were not accepted.',
  429: 'Too many requests. Wait a moment and retry.',
  500: 'The API failed to process the request.',
  502: 'An upstream service is unavailable.',
  503: 'Meridian is in maintenance. Try again shortly.',
  504: 'The API took too long to answer.',
};

/** Normalise anything thrown by HttpClient into the console's one error shape. */
export function toApiError(error: unknown): ApiError {
  if (isApiError(error)) {
    return error;
  }

  if (error instanceof HttpErrorResponse) {
    const body: ApiErrorBody = typeof error.error === 'object' && error.error !== null ? error.error : {};
    const message =
      body.message ??
      body.detail ??
      body.error ??
      STATUS_MESSAGES[error.status] ??
      error.message ??
      'Unexpected API response.';

    return {
      status: error.status,
      code: body.code ?? (error.status === 0 ? 'network' : `http_${error.status}`),
      message: error.status === 0 ? NETWORK_MESSAGE : message,
      fieldErrors: body.fieldErrors,
      reference: body.reference ?? error.headers?.get('X-Request-Id') ?? undefined,
    };
  }

  if (error instanceof Error) {
    return { status: 0, code: 'client', message: error.message };
  }

  return { status: 0, code: 'client', message: 'Unexpected error.' };
}

function isApiError(value: unknown): value is ApiError {
  return (
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    'code' in value &&
    'message' in value
  );
}
