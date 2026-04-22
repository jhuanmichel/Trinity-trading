/**
 * CrashRadar.jsx — Candidatos de crash (bear signals).
 * Wrapper magro sobre RadarBase com direction="bear".
 */
import React, { memo } from 'react'
import RadarBase from './RadarBase'

const CrashRadar = memo((props) => <RadarBase direction="bear" {...props} />)
CrashRadar.displayName = 'CrashRadar'
export default CrashRadar
