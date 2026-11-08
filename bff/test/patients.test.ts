import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import http from 'node:http';
import { AddressInfo } from 'node:net';
import { createApp } from '../src/index.js';
import type { Config } from '../src/config.js';

/**
 * Stub Frappe server — emulates only the documented endpoints the BFF uses:
 *   GET  /api/resource/Patient[?fields&filters&or_filters&limit_page_length&order_by]
 *   GET  /api/resource/Patient/{name}
 *   POST /api/resource/Patient
 */
const patients: Record<string, any>[] = [
  {
    name: 'PAT-2026-00001',
    patient_name: 'Ravi Shankar',
    first_name: 'Ravi',
    last_name: 'Shankar',
    sex: 'Male',
    dob: '1980-04-12',
    mobile: '9820041011',
    email: 'ravi@example.com',
    blood_group: 'B Positive',
    status: 'Active',
    uid: 'UID-1',
  },
  {
    name: 'PAT-2026-00002',
    patient_name: 'Meena Sundaram',
    first_name: 'Meena',
    last_name: 'Sundaram',
    sex: 'Female',
    dob: '1988-09-02',
    mobile: '9841022345',
    status: 'Active',
  },
];
let counter = 2;
let seq = 0;

const stub = http.createServer((req, res) => {
  const u = new URL(req.url ?? '/', 'http://stub');
  const auth = req.headers.authorization ?? '';
  if (auth !== 'token key123:secret456') {
    res.writeHead(403, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ exc_type: 'PermissionError', exception: 'PermissionError: Not permitted' }));
    return;
  }
  const send = (status: number, body: unknown) => {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
  };

  const listMatch = u.pathname === '/api/resource/Patient';
  const getMatch = u.pathname.match(/^\/api\/resource\/Patient\/([^/]+)$/);

  if (req.method === 'GET' && listMatch) {
    let rows = patients.filter(Boolean);
    const orFilters = u.searchParams.get('or_filters');
    const filters = u.searchParams.get('filters');
    if (orFilters) {
      const parsed = JSON.parse(orFilters) as any[][];
      rows = rows.filter((p) =>
        parsed.some(([, field, op, val]) => {
          const v = String(p[field] ?? '');
          if (op === 'like') return v.toLowerCase().includes(String(val).replace(/%/g, '').toLowerCase());
          return v === String(val);
        }),
      );
    }
    if (filters) {
      const parsed = JSON.parse(filters) as any[][];
      rows = rows.filter((p) => parsed.every(([, field, , val]) => String(p[field] ?? '') === String(val)));
    }
    const limit = Number(u.searchParams.get('limit_page_length') ?? 20);
    return send(200, { data: rows.slice(0, limit) });
  }
  if (req.method === 'GET' && getMatch) {
    const doc = patients.find((p) => p?.name === decodeURIComponent(getMatch[1]));
    if (!doc) return send(404, { exc_type: 'DoesNotExistError', exception: 'Patient not found' });
    return send(200, { data: doc });
  }
  if (req.method === 'POST' && listMatch) {
    let body = '';
    req.on('data', (d) => (body += d));
    req.on('end', () => {
      const doc = JSON.parse(body);
      if (!doc.first_name || !doc.sex) {
        return send(417, { _server_messages: JSON.stringify([JSON.stringify({ message: '<b>First Name</b> and <b>Sex</b> are mandatory' })]) });
      }
      const name = `PAT-2026-${String(++counter).padStart(5, '0')}`;
      const created = { name, patient_name: [doc.first_name, doc.last_name].filter(Boolean).join(' '), status: 'Active', ...doc };
      patients.push(created);
      return send(200, { data: created });
    });
    return;
  }
  send(404, { message: 'unknown stub route' });
});

let app: ReturnType<typeof createApp>;
let base: string;

beforeAll(async () => {
  await new Promise<void>((resolve) => stub.listen(0, resolve));
  const port = (stub.address() as AddressInfo).port;
  base = `http://127.0.0.1:${port}`;
  const config: Config = {
    port: 0,
    frappeBaseUrl: base,
    frappeApiKey: 'key123',
    frappeApiSecret: 'secret456',
    bffApiToken: 'test-bff-token',
    allowedOrigins: ['http://localhost:5173'],
    upstreamTimeoutMs: 4000,
    deployMode: 'local',
  };
  app = createApp(config);
});

afterAll(() => stub.close());

const call = (path: string, init?: RequestInit) =>
  app.fetch(
    new Request(`http://bff${path}`, {
      ...init,
      headers: { Authorization: 'Bearer test-bff-token', ...(init?.headers ?? {}) },
    }),
  );

describe('auth guard', () => {
  it('rejects requests without the BFF token', async () => {
    const res = await app.fetch(new Request('http://bff/api/v1/patients/search?q=ravi'));
    expect(res.status).toBe(401);
    expect((await res.json()).error.code).toBe('UNAUTHORIZED');
  });
});

describe('GET /api/v1/patients/search', () => {
  it('maps Frappe documents to the public contract', async () => {
    const res = await call('/api/v1/patients/search?q=ravi');
    expect(res.status).toBe(200);
    const { data } = await res.json();
    expect(data).toHaveLength(1);
    expect(data[0]).toMatchObject({ uhid: 'PAT-2026-00001', name: 'Ravi Shankar', sex: 'M', mobile: '9820041011', bloodGroup: 'B Positive' });
    expect(data[0].age).toBeGreaterThan(40);
  });

  it('searches by mobile', async () => {
    const res = await call('/api/v1/patients/search?q=98410');
    const { data } = await res.json();
    expect(data.map((p: any) => p.uhid)).toEqual(['PAT-2026-00002']);
  });

  it('returns recent patients when q is empty', async () => {
    const res = await call('/api/v1/patients/search');
    expect(res.status).toBe(200);
    expect((await res.json()).data.length).toBeGreaterThan(0);
  });
});

describe('GET /api/v1/patients/:uhid', () => {
  it('returns one patient', async () => {
    const res = await call('/api/v1/patients/PAT-2026-00002');
    expect(res.status).toBe(200);
    expect((await res.json()).data).toMatchObject({ uhid: 'PAT-2026-00002', sex: 'F' });
  });

  it('maps Frappe 404 to NOT_FOUND', async () => {
    const res = await call('/api/v1/patients/NOPE-1');
    expect(res.status).toBe(404);
    expect((await res.json()).error.code).toBe('NOT_FOUND');
  });
});

describe('POST /api/v1/patients', () => {
  it('creates a patient and returns the issued UHID', async () => {
    const before = seq++;
    const res = await call('/api/v1/patients', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Lakshmi Narayanan', sex: 'F', dob: '1972-01-15', mobile: '9841000001' }),
    });
    expect(res.status).toBe(201);
    const { data } = await res.json();
    expect(data.uhid).toMatch(/^PAT-2026-/);
    expect(data).toMatchObject({ name: 'Lakshmi Narayanan', sex: 'F' });
    expect(before).toBeGreaterThanOrEqual(0);
  });

  it('rejects invalid payloads with 422', async () => {
    const res = await call('/api/v1/patients', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'X', sex: 'F', mobile: '9841000002' }),
    });
    expect(res.status).toBe(422);
    expect((await res.json()).error.code).toBe('VALIDATION');
  });

  it('blocks duplicate mobile with 409 and the existing UHID', async () => {
    const res = await call('/api/v1/patients', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Ravi S', sex: 'M', dob: '1980-04-12', mobile: '9820041011' }),
    });
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.error.code).toBe('DUPLICATE');
    expect(body.error.uhid).toBe('PAT-2026-00001');
  });
});

describe('GET /api/v1/health', () => {
  it('reports frappe up with connection target and mode', async () => {
    const res = await call('/api/v1/health');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toMatchObject({ status: 'ok', frappe: 'up', mode: 'local' });
    expect(body.target).toMatch(/^127\.0\.0\.1:\d+$/);
  });
});
