'use client';

/**
 * Hierarchical Product Catalog Drawer / Sidebar
 * Modern meteorological workstation design.
 *
 * Features:
 * - Searchable, collapsable product sidebar organizing all NBM products
 * - 7 Categories: Temp & Moisture, Precip & QPF, Winter, Wind, Convective, Aviation & Marine, Fire
 * - Sub-Selectors: Type, Percentiles, Accumulation Periods, Threshold Exceedances
 * - Quick Search bar with fuzzy filtering
 * - Favorites / Pinned Products saved to localStorage
 * - Responsive: sidebar -> sliding bottom sheet on mobile
 */

import { useEffect, useMemo, useState, useCallback } from 'react';
import { CATEGORIES, searchProducts, type NBMProductDef, type CategoryId, type ProductType, type Accumulation, type Percentile } from './catalog';
import { useFavorites } from './useFavorites';
import type { UnitSystem } from '@/lib/units';

interface ProductSelectorProps {
  selectedProductId: string;
  onSelectProduct: (product: NBMProductDef) => void;
  // Sub-selector state lifted
  selectedType?: ProductType;
  onTypeChange?: (t: ProductType) => void;
  selectedPercentile?: Percentile;
  onPercentileChange?: (p: Percentile) => void;
  selectedAccum?: Accumulation;
  onAccumChange?: (a: Accumulation) => void;
  selectedThreshold?: string;
  onThresholdChange?: (th: string) => void;

  unitSystem?: UnitSystem;
  isOpen?: boolean;
  onClose?: () => void;
  className?: string;
  // Mobile bottom sheet control
  isMobileSheet?: boolean;
}

const TYPE_LABELS: Record<ProductType, { label: string; short: string }> = {
  deterministic: { label: 'Deterministic Guidance', short: 'Det' },
  mean: { label: 'Ensemble Mean', short: 'Mean' },
  probabilistic: { label: 'Probabilistic', short: 'Prob' },
};

export function ProductSelector({
  selectedProductId,
  onSelectProduct,
  selectedType,
  onTypeChange,
  selectedPercentile,
  onPercentileChange,
  selectedAccum,
  onAccumChange,
  selectedThreshold,
  onThresholdChange,
  unitSystem = 'imperial',
  isOpen = true,
  onClose,
  className = '',
  isMobileSheet = false,
}: ProductSelectorProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [collapsedCategories, setCollapsedCategories] = useState<Set<CategoryId>>(new Set());
  const [showOnlyFavorites, setShowOnlyFavorites] = useState(false);
  const { favorites, isFavorite, toggleFavorite, hydrated } = useFavorites();

  // Auto-collapse all except active category on first load
  const activeProduct = useMemo(() => {
    return CATEGORIES.flatMap(c => c.products).find(p => p.id === selectedProductId);
  }, [selectedProductId]);

  useEffect(() => {
    if (activeProduct) {
      // Keep active category expanded, collapse others initially? For UX, expand all by default.
      // We keep all expanded initially.
    }
  }, [activeProduct]);

  const filteredProducts = useMemo(() => {
    if (showOnlyFavorites) {
      const favSet = new Set(favorites);
      return CATEGORIES.flatMap(c => c.products).filter(p => favSet.has(p.id));
    }
    if (searchQuery.trim()) {
      return searchProducts(searchQuery);
    }
    return null; // null means show categorized
  }, [searchQuery, showOnlyFavorites, favorites]);

  const toggleCategory = useCallback((id: CategoryId) => {
    setCollapsedCategories(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const favoriteProducts = useMemo(() => {
    if (!hydrated) return [];
    const favSet = new Set(favorites);
    return CATEGORIES.flatMap(c => c.products).filter(p => favSet.has(p.id));
  }, [favorites, hydrated]);

  return (
    <>
      {/* Backdrop for mobile */}
      {isOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/40 backdrop-blur-sm lg:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={`
          z-40 flex flex-col border-white/10 bg-[#0c1424]/95 backdrop-blur-xl
          shadow-[4px_0_24px_rgba(0,0,0,0.5)]
          ${isMobileSheet
            ? `fixed bottom-0 left-0 right-0 max-h-[78vh] rounded-t-2xl border-t transition-transform duration-300 ${
                isOpen ? 'translate-y-0' : 'translate-y-full'
              }`
            : `fixed left-0 top-[56px] bottom-0 w-[340px] border-r transition-transform duration-300 lg:translate-x-0 ${
                isOpen ? 'translate-x-0' : '-translate-x-full'
              }`
          }
          ${className}
        `}
        aria-label="Product catalog"
      >
        {/* Handle for mobile bottom sheet */}
        {isMobileSheet && (
          <div className="flex justify-center py-2">
            <div className="h-1 w-10 rounded-full bg-white/20" />
          </div>
        )}

        {/* Header: Search + Favorites toggle */}
        <div className="flex flex-col gap-2 border-b border-white/10 p-3">
          <div className="flex items-center justify-between">
            <h2 className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/60">Product Catalog</h2>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setShowOnlyFavorites(v => !v)}
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-semibold transition ${
                  showOnlyFavorites
                    ? 'border-amber-400/30 bg-amber-400/15 text-amber-300'
                    : 'border-white/10 bg-white/5 text-white/50 hover:bg-white/10 hover:text-white/80'
                }`}
                title="Show only favorites"
              >
                <span className="text-[12px]">★</span>
                {showOnlyFavorites ? 'Favorites' : 'All'}
              </button>
              {onClose && (
                <button
                  type="button"
                  onClick={onClose}
                  className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-white/5 text-white/40 hover:bg-white/10 hover:text-white lg:hidden"
                >
                  ✕
                </button>
              )}
            </div>
          </div>

          {/* Quick Search */}
          <div className="relative">
            <svg className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="6" />
              <path d="M21 21l-4.3-4.3" />
            </svg>
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder='Search e.g. "snow" or "gust"'
              className="h-8 w-full rounded-md border border-white/10 bg-[#121d33] pl-8 pr-8 text-[12px] text-white placeholder:text-white/30 outline-none focus:border-sky-500/50 focus:ring-1 focus:ring-sky-500/30"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-0.5 text-white/40 hover:text-white"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>

          {/* Favorites / Pinned */}
          {hydrated && favoriteProducts.length > 0 && !showOnlyFavorites && !searchQuery && (
            <div className="rounded-md border border-amber-500/20 bg-amber-500/5 p-2">
              <div className="mb-1.5 flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-amber-300/80">
                <span>★</span> Pinned Products
                <span className="ml-auto rounded-full bg-amber-500/20 px-1.5 py-0 text-[9px]">{favoriteProducts.length}</span>
              </div>
              <div className="flex flex-col gap-1">
                {favoriteProducts.map(p => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => onSelectProduct(p)}
                    className={`flex items-center justify-between rounded px-2 py-1 text-left text-[11px] transition ${
                      selectedProductId === p.id
                        ? 'bg-white text-[#0c1424] font-semibold'
                        : 'bg-white/5 text-white/70 hover:bg-white/10 hover:text-white'
                    }`}
                  >
                    <span className="flex items-center gap-1.5">
                      <span>{CATEGORIES.find(c => c.id === p.category)?.icon}</span>
                      <span>{p.shortLabel}</span>
                      <span className="text-[9px] opacity-60">{p.element}</span>
                    </span>
                    <span className="text-[10px] opacity-60">{p.units[unitSystem]}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Content scroll */}
        <div className="flex-1 overflow-y-auto overscroll-contain">
          {filteredProducts ? (
            // Flat filtered view
            <div className="p-2">
              <div className="mb-2 text-[10px] text-white/40">
                {filteredProducts.length} products matching &quot;{searchQuery || 'favorites'}&quot;
              </div>
              <div className="flex flex-col gap-1">
                {filteredProducts.map(product => (
                  <ProductRow
                    key={product.id}
                    product={product}
                    isSelected={selectedProductId === product.id}
                    isFavorite={isFavorite(product.id)}
                    onSelect={() => onSelectProduct(product)}
                    onToggleFavorite={() => toggleFavorite(product.id)}
                    unitSystem={unitSystem}
                  />
                ))}
                {filteredProducts.length === 0 && (
                  <div className="py-8 text-center text-[12px] text-white/30">
                    No products found for &quot;{searchQuery}&quot;
                  </div>
                )}
              </div>
            </div>
          ) : (
            // Categorized view
            <div className="flex flex-col">
              {CATEGORIES.map(category => {
                const isCollapsed = collapsedCategories.has(category.id);
                const activeInCategory = category.products.some(p => p.id === selectedProductId);
                return (
                  <div key={category.id} className="border-b border-white/5 last:border-0">
                    <button
                      type="button"
                      onClick={() => toggleCategory(category.id)}
                      className={`flex w-full items-center justify-between px-3 py-2.5 text-left transition hover:bg-white/[0.04] ${
                        activeInCategory ? 'bg-white/[0.03]' : ''
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-[14px]">{category.icon}</span>
                        <div className="flex flex-col">
                          <span className="text-[11px] font-bold uppercase tracking-wide text-white/80">
                            {category.label}
                          </span>
                          <span className="text-[10px] text-white/35">{category.description}</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="rounded-full bg-white/10 px-1.5 py-0.5 text-[9px] font-mono text-white/40">
                          {category.products.length}
                        </span>
                        <svg
                          className={`h-3 w-3 text-white/30 transition-transform ${isCollapsed ? '' : 'rotate-180'}`}
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <path d="M6 9l6 6 6-6" />
                        </svg>
                      </div>
                    </button>

                    {!isCollapsed && (
                      <div className="flex flex-col gap-0.5 bg-[#0a1020] px-2 py-1">
                        {category.products.map(product => (
                          <ProductRow
                            key={product.id}
                            product={product}
                            isSelected={selectedProductId === product.id}
                            isFavorite={isFavorite(product.id)}
                            onSelect={() => onSelectProduct(product)}
                            onToggleFavorite={() => toggleFavorite(product.id)}
                            unitSystem={unitSystem}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Sub-Selectors Panel (when product selected) */}
        {activeProduct && (
          <div className="border-t border-white/10 bg-[#0a1020] p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase tracking-widest text-white/50">Product Options</span>
              <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[9px] font-mono text-sky-300">
                {activeProduct.element.toUpperCase()}
              </span>
            </div>

            {/* Type Selector */}
            {activeProduct.types.length > 1 && (
              <div className="mb-3">
                <label className="mb-1 block text-[10px] font-medium text-white/50">Type</label>
                <div className="flex rounded-md border border-white/10 bg-[#121d33] p-0.5">
                  {activeProduct.types.map(t => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => onTypeChange?.(t)}
                      className={`flex-1 rounded px-2 py-1 text-[10px] font-semibold transition ${
                        (selectedType ?? activeProduct.defaultType) === t
                          ? 'bg-white text-[#0a1020] shadow'
                          : 'text-white/50 hover:text-white/80'
                      }`}
                      title={TYPE_LABELS[t].label}
                    >
                      {TYPE_LABELS[t].short}
                    </button>
                  ))}
                </div>
                <div className="mt-1 text-[9px] text-white/30">
                  {TYPE_LABELS[(selectedType ?? activeProduct.defaultType) as ProductType]?.label}
                </div>
              </div>
            )}

            {/* Percentiles */}
            {activeProduct.percentiles && activeProduct.percentiles.length > 0 && (selectedType === 'probabilistic' || activeProduct.types.includes('probabilistic') || activeProduct.percentiles) && (
              <div className="mb-3">
                <label className="mb-1 block text-[10px] font-medium text-white/50">Percentiles</label>
                <div className="grid grid-cols-6 gap-1">
                  {([10, 25, 50, 75, 90, 95] as Percentile[]).map(p => {
                    const available = !activeProduct.percentiles || activeProduct.percentiles.includes(p);
                    const isSelected = (selectedPercentile ?? activeProduct.defaultPercentile) === p;
                    return (
                      <button
                        key={p}
                        type="button"
                        disabled={!available}
                        onClick={() => available && onPercentileChange?.(p)}
                        className={`rounded border px-1 py-1 text-[10px] font-mono font-semibold transition ${
                          !available
                            ? 'cursor-not-allowed border-white/5 bg-white/5 text-white/20'
                            : isSelected
                              ? 'border-sky-400/50 bg-sky-500/20 text-sky-300'
                              : 'border-white/10 bg-white/5 text-white/50 hover:bg-white/10 hover:text-white'
                        }`}
                      >
                        {p}%
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Accumulation Periods */}
            {activeProduct.accumulations && activeProduct.accumulations.length > 0 && (
              <div className="mb-3">
                <label className="mb-1 block text-[10px] font-medium text-white/50">Accumulation</label>
                <div className="flex flex-wrap gap-1">
                  {(['1h', '6h', '12h', '24h', '48h', '72h'] as Accumulation[]).map(acc => {
                    const available = activeProduct.accumulations?.includes(acc);
                    const isSelected = (selectedAccum ?? activeProduct.defaultAccum) === acc;
                    return (
                      <button
                        key={acc}
                        type="button"
                        disabled={!available}
                        onClick={() => available && onAccumChange?.(acc)}
                        className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold transition ${
                          !available
                            ? 'cursor-not-allowed border-white/5 bg-white/5 text-white/20'
                            : isSelected
                              ? 'border-violet-400/40 bg-violet-500/20 text-violet-300'
                              : 'border-white/10 bg-white/5 text-white/50 hover:border-white/20 hover:text-white'
                        }`}
                      >
                        {acc}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Threshold Exceedances */}
            {activeProduct.thresholds && activeProduct.thresholds.length > 0 && (
              <div className="mb-1">
                <label className="mb-1 block text-[10px] font-medium text-white/50">Threshold Exceedance</label>
                <div className="flex flex-wrap gap-1">
                  {activeProduct.thresholds.map(th => (
                    <button
                      key={th}
                      type="button"
                      onClick={() => onThresholdChange?.(th)}
                      className={`rounded-full border px-2.5 py-1 text-[10px] font-mono transition ${
                        selectedThreshold === th
                          ? 'border-amber-400/40 bg-amber-500/20 text-amber-300'
                          : 'border-white/10 bg-white/5 text-white/50 hover:border-white/20 hover:text-white'
                      }`}
                    >
                      {th}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Product meta */}
            <div className="mt-3 rounded border border-white/5 bg-white/[0.02] px-2 py-1.5">
              <div className="text-[10px] leading-4 text-white/40">
                <span className="font-semibold text-white/60">{activeProduct.label}</span> • {activeProduct.description}
              </div>
              {activeProduct.versionNote && (
                <div className="mt-1 text-[9px] font-mono text-white/25">{activeProduct.versionNote} • Source: NCEP NBM</div>
              )}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="border-t border-white/5 bg-[#080e1c] px-3 py-2">
          <div className="flex items-center justify-between text-[9px] text-white/25">
            <span>{CATEGORIES.flatMap(c => c.products).length} products • NBM v4.2</span>
            <span className="font-mono">AWIPS II • Pivotal style</span>
          </div>
        </div>
      </aside>
    </>
  );
}

function ProductRow({
  product,
  isSelected,
  isFavorite,
  onSelect,
  onToggleFavorite,
  unitSystem,
}: {
  product: NBMProductDef;
  isSelected: boolean;
  isFavorite: boolean;
  onSelect: () => void;
  onToggleFavorite: () => void;
  unitSystem: UnitSystem;
}) {
  return (
    <div
      className={`group flex items-center gap-1 rounded-md border px-2 py-1.5 transition ${
        isSelected
          ? 'border-sky-500/30 bg-sky-500/10'
          : 'border-transparent bg-transparent hover:border-white/10 hover:bg-white/5'
      }`}
    >
      <button
        type="button"
        onClick={onSelect}
        className="flex flex-1 items-center gap-2 text-left"
      >
        <div className={`h-1.5 w-1.5 rounded-full ${isSelected ? 'bg-sky-400' : 'bg-white/20 group-hover:bg-white/40'}`} />
        <div className="flex flex-col">
          <span className={`text-[11px] font-medium leading-none ${isSelected ? 'text-sky-200' : 'text-white/70 group-hover:text-white/90'}`}>
            {product.label}
          </span>
          <span className="text-[9px] font-mono text-white/30">
            {product.element} • {product.shortLabel}
          </span>
        </div>
      </button>

      <span className="ml-auto mr-1 hidden text-[9px] font-mono text-white/25 group-hover:inline md:inline">
        {product.units[unitSystem]}
      </span>

      <button
        type="button"
        onClick={e => {
          e.stopPropagation();
          onToggleFavorite();
        }}
        className={`inline-flex h-5 w-5 items-center justify-center rounded-full transition ${
          isFavorite
            ? 'bg-amber-400/20 text-amber-300'
            : 'bg-white/5 text-white/20 hover:bg-white/10 hover:text-amber-300/70'
        }`}
        title={isFavorite ? 'Remove from favorites' : 'Pin to favorites'}
      >
        <span className="text-[11px]">{isFavorite ? '★' : '☆'}</span>
      </button>
    </div>
  );
}
