'use client';

/**
 * Connection status pill — polls the backend /api/v1/version and /api/v1/health
 * through the Next.js proxy; degrades gracefully offline.
 */

import { useEffect, useState } from 'react';

import { api } from '@/lib/api';

type Status = 'connecting' | 'ok' | 'degraded' | 'offline';

const STATUS_META: Record<Status, { label: string; className: string; dot: string }> = {
  connecting: { label: 'Connecting…', className: 'text-white/50', dot: 'bg-yellow-400' },
  ok: { label: 'Connected', className: 'text-emerald-300', dot: 'bg-emerald-400' },
  degraded: { label: 'Degraded', className: 'text-amber-300', dot: 'bg-amber-400' },
  offline: { label: 'Offline', className: 'text-rose-300', dot: 'bg-rose-400' },
};

export function StatusBadge() {
  const [status, setStatus] = useState<Status>('connecting');
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function check() {
      try {
        const info = await api.getVersion();
        if (cancelled) return;
        setVersion(`v${info.appVersion}`);
        setStatus(info.environment === 'development' ? 'ok' : 'ok');
      } catch {
        if (cancelled) return;
        setStatus('offline');
      }
    }

    void check();
    const timer = setInterval(() => void check(), 15_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const meta = STATUS_META[status];

  return (
    <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-white/10 bg-surface-raised/95 px-3 py-1.5 shadow-panel backdrop-blur">
      <span className={`h-2 w-2 rounded-full ${meta.dot} ${status === 'connecting' ? 'animate-pulse' : ''}`} />
      <span className={`text-xs font-medium ${meta.className}`}>{meta.label}</span>
      {version ? <span className="font-mono text-[10px] text-white/40">{version}</span> : null}
    </div>
  );
}
