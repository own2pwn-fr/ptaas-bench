import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { AbstractControl, ValidationErrors } from '@angular/forms';

/**
 * Turns a control's validation state into the wording used everywhere in Meridian.
 * Nothing is shown until the operator has touched the field or tried to submit.
 */
@Component({
  selector: 'mrd-field-error',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (message(); as text) {
      <p class="field-error">{{ text }}</p>
    }
  `,
  styles: [
    `
      .field-error {
        margin: 0;
        font-size: 12px;
        color: var(--mrd-danger);
      }
    `,
  ],
})
export class FieldErrorComponent {
  readonly control = input<AbstractControl | null>(null);
  readonly label = input('This field');
  /** Set once the form has been submitted, so untouched fields also report. */
  readonly submitted = input(false);

  readonly message = computed(() => {
    const control = this.control();
    if (control === null || control.valid || (!control.touched && !this.submitted())) {
      return '';
    }
    return describe(control.errors, this.label());
  });
}

function describe(errors: ValidationErrors | null, label: string): string {
  if (errors === null) {
    return '';
  }
  if (errors['required']) {
    return `${label} is required.`;
  }
  if (errors['email']) {
    return 'Enter a valid email address.';
  }
  if (errors['minlength']) {
    const detail = errors['minlength'] as { requiredLength: number };
    return `${label} must be at least ${detail.requiredLength} characters.`;
  }
  if (errors['maxlength']) {
    const detail = errors['maxlength'] as { requiredLength: number };
    return `${label} must be ${detail.requiredLength} characters or fewer.`;
  }
  if (errors['min']) {
    const detail = errors['min'] as { min: number };
    return `${label} must be ${detail.min} or more.`;
  }
  if (errors['max']) {
    const detail = errors['max'] as { max: number };
    return `${label} must be ${detail.max} or less.`;
  }
  if (errors['pattern']) {
    return `${label} is not in the expected format.`;
  }
  const custom = errors['message'];
  return typeof custom === 'string' ? custom : `${label} is not valid.`;
}
