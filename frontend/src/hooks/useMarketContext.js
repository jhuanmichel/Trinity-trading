/**
 * useMarketContext.js — Hook composto para contexto de mercado
 */
import { useCurrentState, useSignalHistory, useAltcoinScan, useRadar } from './useSignals'
import { useFunding, useOpenInterest } from './useFunding'

/**
 * Inicializa todos os hooks de dados em um só lugar
 * Chame em App.jsx para ativar todos os polls
 */
export function useMarketContext() {
  const stateQ   = useCurrentState()
  const historyQ = useSignalHistory()
  const altQ     = useAltcoinScan()
  const radar    = useRadar()
  const fundingQ = useFunding()
  const oiQ      = useOpenInterest()

  const isLoading = stateQ.isLoading
  const isError   = stateQ.isError
  const error     = stateQ.error

  return {
    isLoading,
    isError,
    error,
    stateQ,
    historyQ,
    altQ,
    fundingQ,
    oiQ,
    radar,
  }
}

export default useMarketContext
