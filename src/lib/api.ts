/**
 * Typed client for the HIMS BFF (thin backend-for-frontend over Frappe Health).
 *
 * Default base URL is '' → same-origin relative `/api/v1`, which is how the
 * Docker deployment works (nginx proxies /api → bff). If no BFF answers,
 * calls fail fast and the UI falls back to bundled demo data.
 */

export interface PatientSummary {
  uhid: string;
  name: string;
  sex: 'M' | 'F' | 'O' | null;
  dob: string | null;
  age: number | null;
  mobile: string | null;
  email: string | null;
  bloodGroup: string | null;
  status: string | null;
}

export interface CreatePatientInput {
  name: string;
  sex: 'M' | 'F' | 'O';
  dob?: string;
  age?: number;
  mobile: string;
  email?: string;
  bloodGroup?: string;
}

export class BackendUnavailable extends Error {
  constructor() {
    super('BFF unreachable');
    this.name = 'BackendUnavailable';
  }
}

export class ApiError extends Error {
  status: number;
  code: string;
  uhid?: string;
  constructor(status: number, code: string, message: string, uhid?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.uhid = uhid;
  }
}

const BASE: string = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');
const TOKEN: string = import.meta.env.VITE_BFF_TOKEN ?? '';

async function request<T>(path: string, init?: RequestInit, timeoutMs = 5000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
        ...(init?.headers ?? {}),
      },
    });
    const json = await res.json().catch(() => undefined);
    if (!res.ok) {
      const err = json?.error ?? {};
      throw new ApiError(res.status, err.code ?? 'ERROR', err.message ?? `Request failed (${res.status})`, err.uhid);
    }
    return json as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new BackendUnavailable();
  } finally {
    clearTimeout(timer);
  }
}

export async function getBackendHealth(): Promise<'live' | 'demo'> {
  try {
    const res = await request<{ status: string; frappe: string }>('/api/v1/health', undefined, 3000);
    return res.frappe === 'up' ? 'live' : 'demo';
  } catch {
    return 'demo';
  }
}

export interface BackendInfo {
  status: 'ok' | 'degraded';
  frappe: 'up' | 'down';
  /** Host[:port] of the Frappe server the BFF is configured against. */
  target: string;
  /** 'local' = Docker Frappe stack, 'remote' = external Frappe server. */
  mode: 'local' | 'remote';
}

/** Full connection details for the Settings page; null when the BFF is unreachable. */
export async function getBackendInfo(): Promise<BackendInfo | null> {
  try {
    return await request<BackendInfo>('/api/v1/health', undefined, 3000);
  } catch {
    return null;
  }
}

export async function searchPatients(q: string, limit = 20): Promise<PatientSummary[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  const res = await request<{ data: PatientSummary[] }>(`/api/v1/patients/search?${params}`);
  return res.data;
}

export async function createPatient(input: CreatePatientInput): Promise<PatientSummary> {
  const res = await request<{ data: PatientSummary }>('/api/v1/patients', {
    method: 'POST',
    body: JSON.stringify(input),
  });
  return res.data;
}
