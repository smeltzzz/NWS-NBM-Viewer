'use client';

import { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'nbm-favorites-v2';

export function useFavorites() {
  const [favorites, setFavorites] = useState<string[]>([]);
  const [hydrated, setHydrated] = useState(false);

  // Hydrate from localStorage
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          setFavorites(parsed);
        }
      } else {
        // migrate from old key if exists
        const old = localStorage.getItem('nbm-favorites');
        if (old) {
          const parsed = JSON.parse(old);
          if (Array.isArray(parsed)) setFavorites(parsed);
        }
      }
    } catch {
      // ignore
    } finally {
      setHydrated(true);
    }
  }, []);

  // Persist
  useEffect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(favorites));
    } catch {
      // quota or disabled
    }
  }, [favorites, hydrated]);

  const isFavorite = useCallback((id: string) => favorites.includes(id), [favorites]);

  const toggleFavorite = useCallback((id: string) => {
    setFavorites(prev => {
      if (prev.includes(id)) {
        return prev.filter(f => f !== id);
      }
      return [...prev, id];
    });
  }, []);

  const addFavorite = useCallback((id: string) => {
    setFavorites(prev => (prev.includes(id) ? prev : [...prev, id]));
  }, []);

  const removeFavorite = useCallback((id: string) => {
    setFavorites(prev => prev.filter(f => f !== id));
  }, []);

  const clearFavorites = useCallback(() => setFavorites([]), []);

  return {
    favorites,
    hydrated,
    isFavorite,
    toggleFavorite,
    addFavorite,
    removeFavorite,
    clearFavorites,
  };
}
