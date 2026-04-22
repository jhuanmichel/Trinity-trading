/**
 * Skel.jsx — Primitive V2
 * Skeleton loader com shimmer (via classe .t-skel).
 * Props: w (default '100%'), h (default 10), style.
 */
export function Skel({ w = '100%', h = 10, style, ...rest }) {
  return (
    <div
      className="t-skel"
      style={{ width: w, height: h, ...style }}
      {...rest}
    />
  )
}
