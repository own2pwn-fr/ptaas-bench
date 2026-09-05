import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

/** Catch-all for links that no longer resolve — usually old bookmarks. */
@Component({
  selector: 'mrd-not-found',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  template: `
    <div class="page status">
      <p class="status__code">404</p>
      <h1>We could not find that page</h1>
      <p>
        Nothing in Meridian answers to <code>{{ path }}</code>. The screen may have moved in a
        recent release, or the link may have been mistyped.
      </p>
      <div class="status__actions">
        <a class="btn btn--primary" routerLink="/">Back to the overview</a>
        <a class="btn" routerLink="/search">Search Meridian</a>
      </div>
    </div>
  `,
  styleUrl: './status.scss',
})
export class NotFoundComponent {
  readonly path = inject(Router).url;
}
