import type { Metadata, Viewport } from 'next';

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
  themeColor: '#0b1220',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}
