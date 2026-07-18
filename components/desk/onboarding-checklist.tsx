"use client"
import Link from "next/link"
import { useState } from "react"
import { CheckCircle2,ChevronDown,Circle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card,CardContent,CardHeader,CardTitle } from "@/components/ui/card"
export interface SetupStep{label:string;href:string;complete:boolean}
export function OnboardingChecklist({steps}:{steps:SetupStep[]}){const [collapsed,setCollapsed]=useState(false);const complete=steps.every(step=>step.complete);if(complete)return null;return <Card><CardHeader><div className="flex items-center justify-between"><div><CardTitle>Finish setting up your desk</CardTitle><p className="mt-1 text-sm text-muted-foreground">{steps.filter(step=>step.complete).length} of {steps.length} required steps complete</p></div><Button size="sm" variant="ghost" onClick={()=>setCollapsed(!collapsed)} aria-expanded={!collapsed}>{collapsed?"Expand":"Collapse"}<ChevronDown className="ml-2 h-4 w-4"/></Button></div></CardHeader>{!collapsed&&<CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{steps.map(step=><Link key={step.label} href={step.href} className="flex items-center gap-3 rounded-lg border p-3 text-sm hover:bg-accent">{step.complete?<CheckCircle2 className="h-5 w-5 text-emerald-600"/>:<Circle className="h-5 w-5 text-muted-foreground"/>}<span className={step.complete?"text-muted-foreground line-through":"font-medium"}>{step.label}</span></Link>)}</CardContent>}</Card>}
