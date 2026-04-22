/**
 * EmptyState.jsx — Componente unificado para estados sem dados.
 * Substitui divs ad-hoc ("radar-empty", "sh-empty", "alt-empty") por
 * visual consistente com icone + titulo + descricao opcional.
 */
import React from 'react'
import { motion } from 'framer-motion'
import './EmptyState.css'

export default function EmptyState({ icon, title, description, compact = false }) {
  return (
    <motion.div
      className={`empty-state ${compact ? 'empty-state--compact' : ''}`}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
    >
      {icon && <div className="empty-state__icon">{icon}</div>}
      <div className="empty-state__title">{title}</div>
      {description && <div className="empty-state__description">{description}</div>}
    </motion.div>
  )
}
