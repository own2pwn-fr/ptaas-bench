import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { Person } from '../models/domain.model';
import { Page } from '../models/page.model';
import { queryParams } from './http-params.util';

/** Staff directory for the whole group. */
@Injectable({ providedIn: 'root' })
export class DirectoryApi {
  private readonly http = inject(HttpClient);

  people(
    options: { surname?: string; department?: string; page?: number } = {},
  ): Observable<Page<Person>> {
    return this.http.get<Page<Person>>('/api/directory/people', {
      params: queryParams({
        surname: options.surname,
        department: options.department,
        page: options.page,
      }),
    });
  }

  person(uid: string): Observable<Person> {
    return this.http.get<Person>(`/api/directory/people/${uid}`);
  }
}
