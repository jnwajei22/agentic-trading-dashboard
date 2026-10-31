"use client"

import * as React from "react"
import { Moon, Sun } from "lucide-react"
import {usePreferences} from "@/components/desk/preferences-provider"
import { useTheme } from "next-themes"

export function ThemeToggle() {
  const [mounted, setMounted] = React.useState(false)
  const { resolvedTheme, setTheme } = useTheme()
  const {save,ready}=usePreferences();const [error,setError]=React.useState("")
  async function toggle(){const next=resolvedTheme==="dark"?"light":"dark";try{if(ready)await save({appearance:next});else setTheme(next);setError("")}catch{setError("Could not save theme")}}

  React.useEffect(() => {
    setMounted(true)
  }, [])

  return (
    <button
      onClick={() => mounted && void toggle()}
      title={error||"Toggle color theme"}
      disabled={!mounted}
      aria-disabled={!mounted}
      aria-label="Toggle color theme"
      className="relative p-2 hover:bg-gray-100 dark:hover:bg-[#1F1F23] rounded-full transition-colors"
    >
      <Sun className="h-5 w-5 text-gray-600 dark:text-gray-300 transition-all dark:hidden" />
      <Moon className="h-5 w-5 text-gray-600 dark:text-gray-300 transition-all hidden dark:block" />
      <span className="sr-only">Toggle color theme</span>
    </button>
  )
}
