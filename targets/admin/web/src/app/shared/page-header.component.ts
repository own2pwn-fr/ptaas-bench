import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** Title block every screen starts with; actions are projected on the right. */
@Component({
  selector: 'mrd-page-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="header">
      <div class="header__text">
        <h1>{{ title() }}</h1>
        @if (subtitle()) {
          <p class="muted small">{{ subtitle() }}</p>
        }
      </div>
      <div class="header__actions">
        <ng-content select="[actions]" />
      </div>
    </header>
  `,
  styles: [
    `
      .header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 16px;
      }

      .header__text p {
        margin: 0;
      }

      .header__actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
      }
    `,
  ],
})
export class PageHeaderComponent {
  readonly title = input.required<string>();
  readonly subtitle = input('');
}
