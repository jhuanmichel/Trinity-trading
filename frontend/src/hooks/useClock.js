/**
 * useClock.js — Hora local HH:MM:SS, atualiza a cada 1s.
 */
import { useEffect, useState } from 'react'

function fmt(d) {
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, '0'))
    .join(':')
}

export function useClock() {
  const [time, setTime] = useState(() => fmt(new Date()))

  useEffect(() => {
    const id = setInterval(() => setTime(fmt(new Date())), 1000)
    return () => clearInterval(id)
  }, [])

  return time
}
