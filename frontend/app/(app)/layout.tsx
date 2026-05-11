import { redirect } from "next/navigation"
import { getSession } from "@/lib/auth"
import { AppNav } from "@/components/AppNav"
import { AppShell } from "@/components/AppShell"
import { NotificationPermissionRequester } from "@/components/NotificationPermissionRequester"
import { SessionExpiryBanner } from "@/components/SessionExpiryBanner"

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await getSession()
  if (!session) {
    redirect("/login")
  }

  return (
    <AppShell>
      <div className="flex h-screen overflow-hidden">
        <AppNav session={session} />
        <div className="flex-1 flex flex-col overflow-hidden">
          <SessionExpiryBanner tokenExp={session.exp} />
          <main id="main-content" className="flex-1 overflow-auto bg-zinc-50 dark:bg-zinc-950" tabIndex={-1}>
            {children}
          </main>
        </div>
      </div>
      <NotificationPermissionRequester />
    </AppShell>
  )
}
