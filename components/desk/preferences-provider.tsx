"use client"

import {createContext,useCallback,useContext,useEffect,useState} from "react"
import {useTheme} from "next-themes"
import type {UserPreferences} from "@/lib/afd/contracts"
import {browserAfdFetch,mutateAfd} from "@/lib/afd/browser-client"

export const defaultPreferences:UserPreferences={appearance:"system",timezone:"UTC",date_format:"locale",time_format:"locale",currency_display:"account",notifications:{},updated_at:""}
const PreferencesContext=createContext<{preferences:UserPreferences;ready:boolean;save:(patch:Partial<UserPreferences>)=>Promise<UserPreferences>;reload:()=>Promise<void>}>({preferences:defaultPreferences,ready:false,save:async()=>{throw new Error("Preferences unavailable")},reload:async()=>{}})
export function PreferencesProvider({initial,children}:{initial:UserPreferences|null;children:React.ReactNode}){
  const [preferences,setPreferences]=useState(initial??defaultPreferences);const [ready,setReady]=useState(Boolean(initial));const {setTheme}=useTheme()
  useEffect(()=>{if(ready)setTheme(preferences.appearance)},[preferences.appearance,ready,setTheme])
  const reload=useCallback(async()=>{const result=await browserAfdFetch<UserPreferences>("user-preferences");setPreferences(result);setReady(true)},[])
  const save=useCallback(async(patch:Partial<UserPreferences>)=>{const result=await mutateAfd<UserPreferences>("user-preferences","PATCH",patch);setPreferences(result);setReady(true);return result},[])
  return <PreferencesContext.Provider value={{preferences,ready,save,reload}}>{children}</PreferencesContext.Provider>
}
export const usePreferences=()=>useContext(PreferencesContext)
export {formatWorkspaceTime,workspaceDate} from "@/lib/afd/preferences"
