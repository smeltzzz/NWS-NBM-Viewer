import type { Metadata, Viewport } from 'next';

import { AppErrorBoundary } from '@/components/common/ErrorBoundary';
import { ToastProvider } from '@/components/common/ToastProvider';

import './globals.css';

export const metadata: Metadata = {
  title: 'NWS NBM Viewer',
  description:
    'Real-time map viewer for the NOAA/NWS National Blend of Models — deterministic elements, probabilistic percentiles and exceedance thresholds.',
  applicationName: 'NWS NBM Viewer',
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  // 1 = fixed zoom baseline; pinch-zoom is handled by the GL map itself
  // (touchZoomRotate) rather than the browser page, which keeps gestures
  // from double-firing on iOS/Android.
  userScalable: false,
  // Draw under iOS notch/home-indicator; the shell pads with env().
  viewportFit: 'cover',
  themeColor: '#0b1220',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>
        {/* Order matters: the boundary catches shell-level throws; the toast
            provider lives *outside* it so fallbacks can still announce. */}
        <ToastProvider>
          <AppErrorBoundary>{children}</AppErrorBoundary>
        </ToastProvider>
      </body>
    </html>
  );
}
