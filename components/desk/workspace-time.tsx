"use client"
import {usePreferences} from "./preferences-provider"
import {formatWorkspaceTime} from "@/lib/afd/preferences"
export function WorkspaceTime({value,kind="both",empty="Not recorded"}:{value?:string|null;kind?:"time"|"date"|"both";empty?:string}){const {preferences}=usePreferences();return value?<time dateTime={value} title={formatWorkspaceTime(value,preferences)}>{formatWorkspaceTime(value,preferences,kind)}</time>:<>{empty}</>}
