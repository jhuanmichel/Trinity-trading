/**
 * routerStore.js — Router V2 simples baseado em Zustand + persist.
 * Namespace 'trinity:router' (separado de 'trinity:ui' do useSignalStore).
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export const ROUTES = {
  LANDING:   'landing',
  DASHBOARD: 'dashboard',
  SIGNALS:   'signals',
  SETTINGS:  'settings',
}

export const useRouterStore = create(
  persist(
    (set) => ({
      route: ROUTES.DASHBOARD,
      setRoute: (route) => set({ route }),
    }),
    {
      name: 'trinity:router',
      version: 1,
    }
  )
)
