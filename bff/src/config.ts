/**
 * Environment configuration for the BFF.
 * All Frappe credentials stay server-side — the browser never sees them.
 */
export interface Config {
  port: number;
  frappeBaseUrl: string;
  frappeApiKey: string;
  frappeApiSecret: string;
  /** Shared token the frontend must send as `Authorization: Bearer <token>`. Empty = open (local dev only). */
  bffApiToken: string;
  /** Comma-separated allowed origins for CORS. */
  allowedOrigins: string[];
  /** Upstream timeout per Frappe request (ms). */
  upstreamTimeoutMs: number;
  /** 'local' (Docker Frappe stack) or 'remote' (external Frappe server). */
  deployMode: 'local' | 'remote';
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const frappeBaseUrl = (env.FRAPPE_BASE_URL ?? 'http://localhost:8080').replace(/\/+$/, '');
  const host = new URL(frappeBaseUrl).hostname;
  const derivedMode = host === 'frontend' || host === 'localhost' || host === '127.0.0.1' ? 'local' : 'remote';
  const envMode = env.DEPLOY_MODE === 'local' || env.DEPLOY_MODE === 'remote' ? env.DEPLOY_MODE : undefined;
  return {
    port: Number(env.PORT ?? 3001),
    frappeBaseUrl,
    frappeApiKey: env.FRAPPE_API_KEY ?? '',
    frappeApiSecret: env.FRAPPE_API_SECRET ?? '',
    bffApiToken: env.BFF_API_TOKEN ?? '',
    allowedOrigins: (env.FRONTEND_ORIGINS ?? 'http://localhost:5173,http://localhost:3000')
      .split(',')
      .map((o) => o.trim())
      .filter(Boolean),
    upstreamTimeoutMs: Number(env.UPSTREAM_TIMEOUT_MS ?? 8000),
    deployMode: envMode ?? derivedMode,
  };
}
