import { HttpClient } from '@angular/common/http';
import {
  ApplicationRef,
  ChangeDetectionStrategy,
  Component,
  ComponentRef,
  ElementRef,
  EnvironmentInjector,
  OnDestroy,
  createComponent,
  effect,
  inject,
  input,
} from '@angular/core';

import { Notice } from '../../core/models/domain.model';
import { OperationsContextService } from '../../core/services/operations-context.service';

/**
 * Renders the body of an operations notice.
 *
 * Notices are written by the duty supervisor and routinely quote live figures — "{{
 * queueDepth }} documents still in the queue" — so the body is compiled as a template
 * rather than dropped in as text. This is the only place in the console that compiles
 * anything at runtime.
 */
@Component({
  selector: 'mrd-notice-body',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
  styles: [
    `
      :host {
        display: block;
      }

      :host p {
        margin: 0;
      }
    `,
  ],
})
export class NoticeBodyComponent implements OnDestroy {
  readonly notice = input.required<Notice>();

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly environmentInjector = inject(EnvironmentInjector);
  private readonly appRef = inject(ApplicationRef);
  private readonly http = inject(HttpClient);
  private readonly operations = inject(OperationsContextService);

  private current: ComponentRef<unknown> | null = null;

  constructor() {
    effect(() => {
      this.paint(this.notice());
    });
  }

  ngOnDestroy(): void {
    this.discard();
  }

  private paint(notice: Notice): void {
    this.discard();

    const operations = this.operations;
    const definition = Component({
      selector: 'mrd-notice-content',
      template: notice.body,
    })(
      class NoticeContent {
        /** Values a notice may quote. */
        readonly queueDepth = operations.queueDepth();
        readonly openApprovals = operations.openApprovals();
        readonly now = new Date();
      },
    );

    const hostElement = this.host.nativeElement;
    const ref = createComponent(definition, {
      environmentInjector: this.environmentInjector,
      hostElement,
    });
    this.appRef.attachView(ref.hostView);
    ref.changeDetectorRef.detectChanges();
    this.current = ref;

    // The banner is the only place we compile at runtime, so we report what it produced
    // to catch notices that fail silently in production.
    this.http
      .post('/api/client/diagnostics', {
        component: 'notice-banner',
        noticeId: notice.id,
        source: notice.body,
        painted: hostElement.textContent ?? '',
      })
      .subscribe({ error: () => {} });
  }

  private discard(): void {
    if (this.current === null) {
      return;
    }
    this.appRef.detachView(this.current.hostView);
    this.current.destroy();
    this.current = null;
    this.host.nativeElement.replaceChildren();
  }
}
