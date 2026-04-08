/**
 * useFunding.js — Hook para funding rates e open interest
 */
import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { fetchMultipleFunding } from '../engine/fetchFunding'
import { fetchMultipleOI } from '../engine/fetchOpenInterest'
import useSignalStore from '../store/useSignalStore'

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']

export function useFunding() {
  const { setFundingRates } = useSignalStore()

  const query = useQuery({
    queryKey:  ['fundingRates'],
    queryFn:   () => fetchMultipleFunding(SYMBOLS),
    refetchInterval: 300_000,  // 5min
    staleTime: 240_000,
    retry: 1,
  })

  useEffect(() => {
    if (query.data) setFundingRates(query.data)
  }, [query.data])

  return query
}

export function useOpenInterest() {
  const { setOpenInterest } = useSignalStore()

  const query = useQuery({
    queryKey:  ['openInterest'],
    queryFn:   () => fetchMultipleOI(['BTCUSDT', 'ETHUSDT']),
    refetchInterval: 300_000,
    staleTime: 240_000,
    retry: 1,
  })

  useEffect(() => {
    if (query.data) setOpenInterest(query.data)
  }, [query.data])

  return query
}
