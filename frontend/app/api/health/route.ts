import { NextResponse } from 'next/server';

/**
 * Frontend-side health route (used by the Docker prod HEALTHCHECK).
 * Reports whether the Next.js app can reach the backend through the proxy.
 */
export async function GET() {
  const target = process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000';

  let backend: 'ok' | 'unreachable' = 'unreachable';
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2500);
    const response = await fetch(`${target}/health/live`, { signal: controller.signal });
    clearTimeout(timeout);
    backend = response.ok ? 'ok' : 'unreachable';
  } catch {
    backend = 'unreachable';
  }

  return NextResponse.json({
    status: 'ok',
    service: 'nws-nbm-viewer-frontend',
    backend,
    timestamp: new Date().toISOString(),
  });
}
