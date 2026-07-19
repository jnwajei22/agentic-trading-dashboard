export type Side="buy"|"sell"
export type ProtectionMode="price"|"pips"
export type TargetMode=ProtectionMode|"reward_multiple"
export type Quote={bid:number|null;ask:number|null;timestamp?:string|null;source:string;stale:boolean;spread?:number|null}

export function entryForSide(side:Side,quote:Quote|null){return side==="buy"?quote?.ask??null:quote?.bid??null}
export function stepQuantity(value:number,delta:-1|1,step:number,minimum:number,maximum?:number|null){
  const next=Math.round((value+delta*step)*1e8)/1e8
  return Math.min(maximum??Number.POSITIVE_INFINITY,Math.max(minimum,next))
}
export function protectionMessage(kind:"Stop Loss"|"Take Profit",side:Side,price:number|null,entry:number|null){
  if(price==null||entry==null)return null
  const stop=kind==="Stop Loss";const valid=side==="buy"?(stop?price<entry:price>entry):(stop?price>entry:price<entry)
  if(valid)return null
  const relation=side==="buy"?(stop?"below":"above"):(stop?"above":"below")
  return `${kind} must be ${relation} the entry price for a ${side==="buy"?"Buy":"Sell"} order.`
}
export function activeCalculation<T>(live:T|null,preview:{calculation:T;expires_at:string}|null){return preview?.calculation??live}
export function previewExpired(preview:{expires_at:string}|null,now=Date.now()){return Boolean(preview&&new Date(preview.expires_at).getTime()<=now)}
