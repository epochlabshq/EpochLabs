// GoForge API client. Always relative (`/api/...`): the session is an HttpOnly cookie, so the requests must be same-origin.
// next.config.ts rewrites /api/* to the backend, which makes that work in dev and in production.
import type {
  ApiErrorDetail, LaunchesPayload, Me, OwnIdea, RoundPayload, Scoreboard,
} from '@/components/goforge/types';

export class ApiError extends Error {
  status: number;
  detail: ApiErrorDetail;

  constructor(status: number, detail: ApiErrorDetail) {
    super(detail.message || detail.code);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/goforge${path}`, { cache: 'no-store', credentials: 'same-origin', ...init });
  if (!res.ok) {
    let detail: ApiErrorDetail = { code: `http_${res.status}`, message: `Request failed (${res.status}).` };
    try {
      const body = await res.json();
      if (body?.detail && typeof body.detail === 'object') detail = body.detail;
      else if (typeof body?.detail === 'string') detail = { code: `http_${res.status}`, message: body.detail };
    } catch {
      /* not JSON: keep the generic detail */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

export const gfApi = {
  round: () => request<RoundPayload>('/round'),
  launches: () => request<LaunchesPayload>('/launches'),
  me: () => request<Me>('/auth/me'),
  // The wallet hands over a lower-case address; the server returns its EIP-55 form, which is what a SIWE message must carry
  nonce: (address: string) => request<{ address: string | null; nonce: string; domain: string; uri: string; chain_id: number; statement: string; issued_at: string }>(`/auth/nonce?address=${encodeURIComponent(address)}`),
  siwe: (message: string, signature: string) => request<Me>('/auth/siwe', json({ message, signature })),
  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),
  myIdeas: () => request<{ ideas: OwnIdea[] }>('/ideas/mine'),
  scoreboard: (date: string) => request<Scoreboard>(`/scoreboard/${encodeURIComponent(date)}`),
  typedData: (voter: string, ideaId: string) =>
    request<Record<string, unknown>>(`/vote/typed-data?voter=${encodeURIComponent(voter)}&idea_id=${encodeURIComponent(ideaId)}`),
  vote: (body: { voter: string; idea_id: string; round: string; signed_at: number; signature: string }) =>
    request<{ idea_id: string; changed: boolean; votes: Record<string, number> }>('/vote', json(body)),
  submit: (form: FormData) => request<OwnIdea>('/ideas', { method: 'POST', body: form }),
  xStartUrl: '/api/goforge/auth/x',
  votesUrl: (date: string) => `/api/goforge/votes/${encodeURIComponent(date)}`,
};
