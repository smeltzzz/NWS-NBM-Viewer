import { MapShell } from '@/components/map/MapShell';

/**
 * The single-page map viewer. The layout is locked to the viewport
 * (100vh / overflow hidden) by the global stylesheet.
 */
export default function HomePage() {
  return (
    <main className="h-screen w-screen overflow-hidden">
      <MapShell />
    </main>
  );
}
