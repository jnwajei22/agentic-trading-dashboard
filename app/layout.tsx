import type { Metadata } from "next"
import { ThemeProvider } from "@/components/theme-provider"
import { Toaster } from "@/components/ui/sonner"
import "./globals.css"

export const metadata: Metadata = {
  title: { default: "Agentic Trading Desk", template: "%s | Agentic Trading Desk" },
  description: "Market research, trade execution, and autonomous trading workspace",
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" suppressHydrationWarning><body>
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>{children}<Toaster richColors closeButton /></ThemeProvider>
  </body></html>
}
