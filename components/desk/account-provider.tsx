"use client"
import { createContext,useContext,useMemo,useState } from "react"
import { useRouter } from "next/navigation"
import type { Account } from "@/lib/afd/contracts"
import { normalizeAccountContext,selectedAccount,type GlobalAccountContext } from "@/lib/afd/account-context"
import { mutateAfd } from "@/lib/afd/browser-client"
type Value={accounts:Account[];selected:GlobalAccountContext|null;selecting:boolean;select:(publicRef:string)=>Promise<void>}
const Context=createContext<Value|null>(null)
export function AccountProvider({accounts,children}:{accounts:Account[];children:React.ReactNode}){const router=useRouter();const [selecting,setSelecting]=useState(false);const selected=useMemo(()=>{const account=selectedAccount(accounts);return account?normalizeAccountContext(account):null},[accounts]);async function select(publicRef:string){setSelecting(true);try{await mutateAfd(`broker/accounts/${encodeURIComponent(publicRef)}/default`,"PUT");router.refresh()}finally{setSelecting(false)}}return <Context.Provider value={{accounts,selected,selecting,select}}>{children}</Context.Provider>}
export function useAccountContext(){const value=useContext(Context);if(!value)throw new Error("Account context is unavailable.");return value}
