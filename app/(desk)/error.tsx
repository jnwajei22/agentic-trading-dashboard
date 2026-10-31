"use client"
import {useRouter} from "next/navigation"
import {Button} from "@/components/ui/button"
export default function WorkspaceError({reset}:{reset:()=>void}){const router=useRouter();return <div role="alert" className="mx-auto max-w-xl rounded-3xl bg-card p-8"><h1 className="text-xl font-semibold">This page couldn’t load</h1><p className="mt-3 text-sm leading-relaxed text-muted-foreground">The dashboard couldn’t read the latest backend data. Saved settings and trading controls have not been changed.</p><Button className="mt-6" variant="outline" onClick={()=>{router.refresh();reset()}}>Try again</Button></div>}
