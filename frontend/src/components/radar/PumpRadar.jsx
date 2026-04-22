/**
 * PumpRadar.jsx — Candidatos de pump (bull signals).
 * Wrapper magro sobre RadarBase com direction="bull".
 */
import React, { memo } from 'react'
import RadarBase from './RadarBase'

const PumpRadar = memo((props) => <RadarBase direction="bull" {...props} />)
PumpRadar.displayName = 'PumpRadar'
export default PumpRadar
