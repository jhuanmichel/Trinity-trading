/**
 * Sidebar.jsx — Navigation sidebar
 */
import React, { memo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import useSignalStore from '../../store/useSignalStore'
import './Sidebar.css'

const NAV_ITEMS = [
  { id: 'signals',  icon: '◈', label: 'SIGNALS',  sub: 'BTC/USDT SMC' },
  { id: 'altcoins', icon: '◉', label: 'ALTCOINS', sub: 'Radar Scanner' },
  { id: 'radar',    icon: '◎', label: 'RADAR',    sub: 'Crash / Pump' },
  { id: 'market',   icon: '◇', label: 'MARKET',   sub: 'Context' },
]

const Sidebar = memo(() => {
  const { sidebarOpen, activeTab, setActiveTab } = useSignalStore()

  return (
    <AnimatePresence initial={false}>
      {sidebarOpen && (
        <motion.aside
          className="sidebar"
          initial={{ x: -220, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: -220, opacity: 0 }}
          transition={{ duration: 0.2, ease: 'easeInOut' }}
        >
          <nav className="sidebar-nav">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                className={`sidebar-item${activeTab === item.id ? ' active' : ''}`}
                onClick={() => setActiveTab(item.id)}
              >
                <span className="sidebar-icon">{item.icon}</span>
                <div className="sidebar-labels">
                  <span className="sidebar-label">{item.label}</span>
                  <span className="sidebar-sub">{item.sub}</span>
                </div>
                {activeTab === item.id && (
                  <motion.div
                    className="sidebar-indicator"
                    layoutId="sidebar-indicator"
                    transition={{ duration: 0.2 }}
                  />
                )}
              </button>
            ))}
          </nav>

          <div className="sidebar-footer">
            <div className="sidebar-footer-brand">TRINITY v3.0</div>
            <div className="sidebar-footer-sub">Smart Money Engine</div>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  )
})

Sidebar.displayName = 'Sidebar'
export default Sidebar
