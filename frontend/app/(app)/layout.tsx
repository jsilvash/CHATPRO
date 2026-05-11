import { redirect } from "next/navigation"
import { getSession } from "@/lib/auth"
import { AppNav } from "@/components/AppNav"
import { NotificationPermissionRequester } from "@/components/NotificationPermissionRequester"
import { SessionExpiryBanner } from "@/components/SessionExpiryBanner"

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await getSession()
  if (!session) {
    redirect("/login")
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <AppNav session={session} />
      {/* En móvil el nav es fixed, por eso flex-1 ocupa todo el ancho */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <SessionExpiryBanner tokenExp={session.exp} />
        {/* pt-12 en móvil para dejar espacio al botón hamburguesa */}
        <main
          id="main-content"
          className="flex-1 overflow-auto bg-zinc-50 dark:bg-zinc-950 pt-12 md:pt-0"
          tabIndex={-1}
        >
          {children}
        </main>
      </div>
      <NotificationPermissionRequester />
    </div>
  )
}
