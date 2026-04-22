/**
 * Shell.jsx — Container V2: TopBar + Ticker + <main> + Footer.
 */
import { T } from '@/styles/tokens'
import { TopBar } from './TopBar'
import { Ticker } from './Ticker'
import { Footer } from './Footer'

export function Shell({ children }) {
  return (
    <div style={{ minHeight: '100vh', background: T.bg, color: T.text }}>
      <TopBar />
      <Ticker />
      <main>{children}</main>
      <Footer />
    </div>
  )
}
