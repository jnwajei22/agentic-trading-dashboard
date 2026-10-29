const PROVIDERS:Record<string,string>={
  finnhub:"Finnhub",
  kraken:"Kraken",
  bls:"BLS",
  bea:"BEA",
  ecb:"ECB",
  fred:"FRED",
  tradingview:"TradingView",
  tradelocker:"TradeLocker",
}

export function normalizeSourceProviders(value:string){
  return value.split(/\s*[,/]\s*/).map(provider=>{
    const trimmed=provider.trim()
    return PROVIDERS[trimmed.toLowerCase()]??trimmed
  }).filter(Boolean).join(" · ")
}
