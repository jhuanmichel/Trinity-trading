/**
 * useKeyboard.js — Keyboard shortcuts
 */
import { useEffect } from 'react'
import { toast } from 'react-hot-toast'
import useSignalStore from '../store/useSignalStore'

const SHORTCUTS = {
  '1': 'signals',
  '2': 'altcoins',
  '3': 'radar',
  '4': 'market',
}

export function useKeyboard() {
  const { setActiveTab, toggleSidebar } = useSignalStore()

  useEffect(() => {
    function handler(e) {
      // Ignora se em input/textarea
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return

      // Tab shortcuts: 1-4
      if (SHORTCUTS[e.key]) {
        const tab = SHORTCUTS[e.key]
        setActiveTab(tab)
        toast(`Tab: ${tab.toUpperCase()}`, {
          duration: 1000,
          position: 'bottom-right',
          className: 'trinity-toast',
        })
        return
      }

      // Sidebar: s
      if (e.key === 's' && !e.ctrlKey && !e.metaKey) {
        toggleSidebar()
        return
      }

      // Refresh: r
      if (e.key === 'r' && !e.ctrlKey && !e.metaKey) {
        window.location.reload()
        return
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [setActiveTab, toggleSidebar])
}
