'use client';

/**
 * Top Application Bar — modern meteorological workstation design.
 * Mirrors Pivotal Weather / College of DuPage / AWIPS II top bars.
 *
 * Features:
 * - Model Run Cycle Selector with LATEST badge + ingestion progress
 * - Domain Switcher as segmented pill control
 * - Unit Toggle (Imperial vs Metric)
 * - Theme toggle & fullscreen button
 * - Responsive: collapses to hamburger + bottom sheet on mobile
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { NbmDomain } from '@/lib/types';
import { DOMAINS } from '@/lib/nbm';
import type { UnitSystem } from '@/lib/units';
import { formatCycle } from '@/lib/format';

interface RunOption {
  cycle: string; // YYYYMMDDHH
  label: string; // YYYY-MM-DD HH:00 UTC
  isLatest: boolean;
  status: 'complete' | 'ingesting' | 'pending';
  progress?: number; // 0-100 if ingesting
}

interface HeaderProps {
  // Domain
  domain: NbmDomain;
  onDomainChange: (d: NbmDomain) => void;
  // Cycle
  cycle: string; // YYYYMMDDHH
  onCycleChange?: (cycle: string) => void;
  availableCycles?: string[]; // optional override, else generate recent
  runStatus?: 'complete' | 'ingesting' | 'pending';
  ingestionProgress?: number;
  // Units
  unitSystem: UnitSystem;
  onUnitChange: (u: UnitSystem) => void;
  // Theme
  theme?: 'dark' | 'light';
  onThemeToggle?: () => void;
  // Extras
  onToggleSidebar?: () => void;
  sidebarOpen?: boolean;
}

function generateRecentCycles(count = 12, baseCycle?: string): RunOption[] {
  // If baseCycle provided, use it as latest, else compute from now
  let baseDate: Date;
  if (baseCycle && /^\d{10}$/.test(baseCycle)) {
    const y = parseInt(baseCycle.slice(0, 4), 10);
    const m = parseInt(baseCycle.slice(4, 6), 10) - 1;
    const d = parseInt(baseCycle.slice(6, 8), 10);
    const h = parseInt(baseCycle.slice(8, 10), 10);
    baseDate = new Date(Date.UTC(y, m, d, h));
  } else {
    baseDate = new Date();
    // Round down to last hour if minutes < 40, else current hour (NBM latency)
    if (baseDate.getUTCMinutes() < 40) {
      baseDate.setUTCHours(baseDate.getUTCHours() - 1);
    }
    baseDate.setUTCMinutes(0, 0, 0);
  }

  const runs: RunOption[] = [];
  for (let i = 0; i < count; i++) {
    const dt = new Date(baseDate);
    dt.setUTCHours(dt.getUTCHours() - i);
    const pad = (n: number) => String(n).padStart(2, '0');
    const cycleStr = `${dt.getUTCFullYear()}${pad(dt.getUTCMonth() + 1)}${pad(dt.getUTCDate())}${pad(dt.getUTCHours())}`;
    const label = `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())} ${pad(dt.getUTCHours())}:00 UTC`;
    const isLatest = i === 0;
    // Simulate status: latest might be ingesting if recent, older are complete
    let status: RunOption['status'] = 'complete';
    let progress: number | undefined;
    if (isLatest) {
      const ageMin = (Date.now() - dt.getTime()) / 60000;
      if (ageMin < 45) {
        status = 'ingesting';
        progress = Math.min(95, Math.max(15, Math.round(30 + (45 - ageMin) * 1.5)));
      }
    }
    runs.push({ cycle: cycleStr, label, isLatest, status, progress });
  }
  return runs;
}

const DOMAIN_PILLS: { code: NbmDomain; label: string; short: string }[] = [
  { code: 'co', label: 'CONUS', short: 'CONUS' },
  { code: 'ak', label: 'ALASKA', short: 'AK' },
  { code: 'hi', label: 'HAWAII', short: 'HI' },
  { code: 'pr', label: 'PUERTO RICO', short: 'PR' },
  { code: 'gu', label: 'GUAM', short: 'GU' },
  { code: 'oc', label: 'OCEANIC', short: 'OCN' },
];

export function Header({
  domain,
  onDomainChange,
  cycle,
  onCycleChange,
  availableCycles,
  runStatus,
  ingestionProgress,
  unitSystem,
  onUnitChange,
  theme = 'dark',
  onThemeToggle,
  onToggleSidebar,
  sidebarOpen,
}: HeaderProps) {
  const [cycleDropdownOpen, setCycleDropdownOpen] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Generate runs
  const runOptions = useMemo(() => {
    if (availableCycles && availableCycles.length > 0) {
      return availableCycles.map((c, idx) => {
        const label = /^\d{10}$/.test(c)
          ? `${c.slice(0, 4)}-${c.slice(4, 6)}-${c.slice(6, 8)} ${c.slice(8, 10)}:00 UTC`
          : c;
        return {
          cycle: c,
          label,
          isLatest: idx === 0,
          status: (idx === 0 ? runStatus : 'complete') as RunOption['status'],
          progress: idx === 0 ? ingestionProgress : undefined,
        };
      });
    }
    return generateRecentCycles(16, cycle);
  }, [availableCycles, cycle, runStatus, ingestionProgress]);

  const activeRun = useMemo(
    () => runOptions.find(r => r.cycle === cycle) ?? runOptions[0],
    [runOptions, cycle],
  );

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setCycleDropdownOpen(false);
      }
    }
    if (cycleDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [cycleDropdownOpen]);

  // Fullscreen listener
  useEffect(() => {
    function onFsChange() {
      setIsFullscreen(!!document.fullscreenElement);
    }
    document.addEventListener('fullscreenchange', onFsChange);
    return () => document.removeEventListener('fullscreenchange', onFsChange);
  }, []);

  const toggleFullscreen = useCallback(async () => {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch {
      // ignore
    }
  }, []);

  const handleThemeToggle = useCallback(() => {
    if (onThemeToggle) {
      onThemeToggle();
      return;
    }
    // fallback: toggle class on html
    const html = document.documentElement;
    const isDark = html.classList.contains('dark');
    if (isDark) {
      html.classList.remove('dark');
      html.classList.add('light');
      try { localStorage.setItem('nbm-theme', 'light'); } catch {}
    } else {
      html.classList.remove('light');
      html.classList.add('dark');
      try { localStorage.setItem('nbm-theme', 'dark'); } catch {}
    }
  }, [onThemeToggle]);

  return (
    <header className="relative z-40 flex h-[56px] w-full items-center justify-between gap-2 border-b border-white/10 bg-[#0b121e]/95 px-2 backdrop-blur-xl supports-[backdrop-filter]:bg-[#0b121e]/80 md:px-4">
      {/* Left: Logo + Sidebar toggle + Domain pills (desktop) */}
      <div className="flex items-center gap-2 md:gap-4">
        {/* Sidebar toggle (mobile/tablet) */}
        {onToggleSidebar && (
          <button
            type="button"
            onClick={onToggleSidebar}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-white/10 bg-white/5 text-white/70 transition hover:bg-white/10 hover:text-white md:hidden"
            aria-label={sidebarOpen ? 'Close product drawer' : 'Open product drawer'}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d={sidebarOpen ? 'M6 18L18 6M6 6l12 12' : 'M4 6h16M4 12h16M4 18h16'} />
            </svg>
          </button>
        )}

        {/* Logo */}
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-sky-400 to-indigo-600 text-[11px] font-black tracking-widest text-white shadow">
            NBM
          </div>
          <div className="hidden flex-col leading-none md:flex">
            <span className="text-[12px] font-bold tracking-wide text-white">NWS NBM VIEWER</span>
            <span className="text-[9px] font-medium tracking-[0.18em] text-white/40">NATIONAL BLEND v4.2</span>
          </div>
        </div>

        {/* Domain Switcher - desktop pill control */}
        <div className="hidden items-center rounded-full border border-white/10 bg-[#121a2a] p-0.5 shadow-inner lg:flex">
          {DOMAIN_PILLS.map(d => {
            const active = d.code === domain;
            return (
              <button
                key={d.code}
                type="button"
                onClick={() => onDomainChange(d.code)}
                className={`relative rounded-full px-3 py-1 text-[11px] font-semibold tracking-wide transition-all ${
                  active
                    ? 'bg-white text-[#0b121e] shadow'
                    : 'text-white/55 hover:bg-white/10 hover:text-white/90'
                }`}
                title={DOMAINS.find(dd => dd.code === d.code)?.label ?? d.label}
              >
                {d.label}
              </button>
            );
          })}
        </div>

        {/* Domain Switcher - mobile dropdown / compact pills */}
        <div className="flex items-center gap-1 lg:hidden">
          <div className="flex rounded-full border border-white/10 bg-[#121a2a] p-0.5">
            {DOMAIN_PILLS.slice(0, 3).map(d => (
              <button
                key={d.code}
                type="button"
                onClick={() => onDomainChange(d.code)}
                className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${
                  d.code === domain ? 'bg-white text-black' : 'text-white/50'
                }`}
              >
                {d.short}
              </button>
            ))}
            {/* More domains in a small overflow */}
            <select
              value={DOMAIN_PILLS.some(p => p.code === domain && ['co','ak','hi'].includes(p.code)) ? '' : domain}
              onChange={e => onDomainChange(e.target.value as NbmDomain)}
              className="ml-1 rounded-full bg-transparent px-1 text-[10px] font-bold text-white/60 outline-none"
            >
              <option value="" disabled>More</option>
              {DOMAIN_PILLS.slice(3).map(d => (
                <option key={d.code} value={d.code} className="bg-[#121a2a] text-white">
                  {d.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Center: Model Run Cycle Selector */}
      <div className="flex items-center gap-2">
        <div className="relative" ref={dropdownRef}>
          <button
            type="button"
            onClick={() => setCycleDropdownOpen(o => !o)}
            className="group flex items-center gap-2 rounded-md border border-white/15 bg-[#121a2a] px-2.5 py-1.5 text-left shadow-sm transition hover:border-white/25 hover:bg-[#16213a] md:px-3"
          >
            <div className="flex flex-col">
              <div className="flex items-center gap-1.5">
                <span className="text-[9px] font-semibold uppercase tracking-widest text-white/40">Model Cycle</span>
                {activeRun?.isLatest && (
                  <span className="inline-flex items-center rounded-full bg-emerald-500/20 px-1.5 py-0 text-[8px] font-black tracking-widest text-emerald-400 ring-1 ring-emerald-500/30">
                    LATEST
                  </span>
                )}
                {activeRun?.status === 'ingesting' && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/20 px-1.5 py-0 text-[8px] font-bold tracking-wide text-amber-300 ring-1 ring-amber-500/30">
                    <span className="h-1 w-1 animate-pulse rounded-full bg-amber-400" />
                    INGESTING
                  </span>
                )}
              </div>
              <span className="font-mono text-[12px] font-medium text-white md:text-[13px]">
                {activeRun?.label ?? formatCycle(cycle)}
              </span>
            </div>
            <svg
              className={`ml-1 h-3 w-3 text-white/40 transition-transform ${cycleDropdownOpen ? 'rotate-180' : ''}`}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>

          {/* Ingestion progress bar (when ingesting) */}
          {activeRun?.status === 'ingesting' && typeof activeRun.progress === 'number' && (
            <div className="absolute -bottom-1 left-1 right-1 h-0.5 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full bg-gradient-to-r from-amber-400 to-orange-400 transition-all"
                style={{ width: `${activeRun.progress}%` }}
              />
            </div>
          )}

          {/* Dropdown */}
          {cycleDropdownOpen && (
            <div className="absolute left-0 top-[calc(100%+8px)] z-50 max-h-[320px] w-[300px] overflow-hidden rounded-lg border border-white/10 bg-[#0f182b] shadow-2xl backdrop-blur-xl">
              <div className="border-b border-white/5 bg-white/[0.02] px-3 py-2">
                <div className="text-[10px] font-semibold uppercase tracking-widest text-white/50">Recent Model Runs</div>
                <div className="text-[10px] text-white/30">NBM publishes hourly • latency ~20-40min</div>
              </div>
              <div className="max-h-[260px] overflow-y-auto py-1">
                {runOptions.map(run => (
                  <button
                    key={run.cycle}
                    type="button"
                    onClick={() => {
                      onCycleChange?.(run.cycle);
                      setCycleDropdownOpen(false);
                    }}
                    className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition hover:bg-white/5 ${
                      run.cycle === cycle ? 'bg-sky-500/10 text-sky-300' : 'text-white/70'
                    }`}
                  >
                    <div className="flex flex-col">
                      <span className="font-mono text-[12px]">{run.label}</span>
                      <span className="text-[10px] text-white/35">Cycle {run.cycle} • {run.status}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      {run.isLatest && (
                        <span className="rounded-full bg-emerald-500/20 px-1.5 py-0.5 text-[8px] font-bold tracking-widest text-emerald-400">
                          LATEST
                        </span>
                      )}
                      {run.status === 'ingesting' && (
                        <span className="text-[10px] text-amber-300">{run.progress}%</span>
                      )}
                      {run.cycle === cycle && (
                        <span className="h-1.5 w-1.5 rounded-full bg-sky-400" />
                      )}
                    </div>
                  </button>
                ))}
              </div>
              <div className="border-t border-white/5 bg-white/[0.02] px-3 py-1.5 text-[10px] text-white/25">
                Data via NOMADS / NCEP • Updated every ~10 min
              </div>
            </div>
          )}
        </div>

        {/* Run initialization status dot (always visible) */}
        <div className="hidden items-center gap-1.5 rounded-full border border-white/10 bg-[#121a2a] px-2.5 py-1 md:flex">
          <span
            className={`h-2 w-2 rounded-full ${
              activeRun?.status === 'complete'
                ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.6)]'
                : activeRun?.status === 'ingesting'
                  ? 'animate-pulse bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.6)]'
                  : 'bg-white/20'
            }`}
          />
          <span className="text-[10px] font-medium tracking-wide text-white/60">
            {activeRun?.status === 'complete' ? 'Ready' : activeRun?.status === 'ingesting' ? `Ingesting ${activeRun.progress ?? ''}%` : 'Pending'}
          </span>
        </div>
      </div>

      {/* Right: Unit Toggle, Theme, Fullscreen */}
      <div className="flex items-center gap-1.5 md:gap-2">
        {/* Unit Toggle */}
        <div className="flex items-center rounded-full border border-white/10 bg-[#121a2a] p-0.5">
          <button
            type="button"
            onClick={() => onUnitChange('imperial')}
            className={`rounded-full px-2.5 py-1 text-[11px] font-semibold transition ${
              unitSystem === 'imperial'
                ? 'bg-white text-[#0b121e] shadow'
                : 'text-white/50 hover:text-white/80'
            }`}
            title="Imperial: °F, in, mph, kts"
          >
            <span className="hidden md:inline">°F / in / mph</span>
            <span className="md:hidden">IMP</span>
          </button>
          <button
            type="button"
            onClick={() => onUnitChange('metric')}
            className={`rounded-full px-2.5 py-1 text-[11px] font-semibold transition ${
              unitSystem === 'metric'
                ? 'bg-white text-[#0b121e] shadow'
                : 'text-white/50 hover:text-white/80'
            }`}
            title="Metric: °C, mm, km/h, m/s"
          >
            <span className="hidden md:inline">°C / mm / km/h</span>
            <span className="md:hidden">MET</span>
          </button>
        </div>

        {/* Theme toggle */}
        <button
          type="button"
          onClick={handleThemeToggle}
          className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-[#121a2a] text-white/60 transition hover:bg-white/10 hover:text-white"
          aria-label="Toggle theme"
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        >
          {theme === 'dark' ? (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
            </svg>
          )}
        </button>

        {/* Fullscreen */}
        <button
          type="button"
          onClick={toggleFullscreen}
          className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-[#121a2a] text-white/60 transition hover:bg-white/10 hover:text-white"
          aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
          title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            {isFullscreen ? (
              <path d="M9 9L4 4m0 0v5m0-5h5M15 9l5-5m0 0v5m0-5h-5M9 15l-5 5m0 0v-5m0 5h5M15 15l5 5m0 0v-5m0 5h-5" />
            ) : (
              <path d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4" />
            )}
          </svg>
        </button>

        {/* Status / version chip - desktop only */}
        <div className="hidden items-center gap-2 rounded-full border border-white/5 bg-white/[0.03] px-2.5 py-1 md:flex">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
          <span className="text-[10px] font-mono tracking-wide text-white/40">NBM • LIVE</span>
        </div>
      </div>
    </header>
  );
}
