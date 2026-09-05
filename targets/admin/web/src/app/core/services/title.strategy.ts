import { Injectable, inject } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { RouterStateSnapshot, TitleStrategy } from '@angular/router';

/** Keeps every tab titled "<screen> — Meridian", matching the group's other tools. */
@Injectable()
export class MeridianTitleStrategy extends TitleStrategy {
  private readonly title = inject(Title);

  override updateTitle(snapshot: RouterStateSnapshot): void {
    const screen = this.buildTitle(snapshot);
    this.title.setTitle(screen ? `${screen} — Meridian` : 'Meridian — Calderwood Group');
  }
}
