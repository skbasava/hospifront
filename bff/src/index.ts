import { Hono } from 'hono';
import { cors } from 'hono/cors';
import { serve } from '@hono/node-server';
import { loadConfig, type Config } from './config.js';
import { FrappeClient } from './frappe.js';
import { patientsRoutes, onRouteError } from './patients.js';

export function createApp(config: Config) {
  const frappe = new FrappeClient(config);
  const app = new Hono();

  app.use(
    '*',
    cors({
      origin: (origin) => (config.allowedOrigins.includes(origin) ? origin : null),
      allowHeaders: ['Content-Type', 'Authorization'],
      allowMethods: ['GET', 'POST', 'OPTIONS'],
      maxAge: 600,
    }),
  );

  // Shared-token guard (placeholder until full auth lands with the security phase).
  app.use('/api/*', async (c, next) => {
    if (!config.bffApiToken) return next();
    const auth = c.req.header('Authorization') ?? '';
    if (auth !== `Bearer ${config.bffApiToken}`) {
      return c.json({ error: { code: 'UNAUTHORIZED', message: 'Missing or invalid BFF token' } }, 401);
    }
    return next();
  });

  app.get('/api/v1/health', async (c) => {
    const target = new URL(config.frappeBaseUrl).host;
    try {
      await frappe.ping();
      return c.json({ status: 'ok', frappe: 'up', target, mode: config.deployMode });
    } catch {
      return c.json({ status: 'degraded', frappe: 'down', target, mode: config.deployMode }, 503);
    }
  });

  app.route('/api/v1/patients', patientsRoutes(frappe));

  app.onError((err, c) => onRouteError(err, c));
  app.notFound((c) => c.json({ error: { code: 'NOT_FOUND', message: 'Unknown route' } }, 404));
  return app;
}

// Start only when run directly (not when imported by tests).
if (import.meta.url === `file://${process.argv[1]}`) {
  const config = loadConfig();
  const app = createApp(config);
  serve({ fetch: app.fetch, port: config.port }, (info) => {
    console.log(`HIMS BFF listening on :${info.port} → Frappe at ${config.frappeBaseUrl}`);
  });
}
