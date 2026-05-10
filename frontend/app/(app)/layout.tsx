import { redirect } from "next/navigation"
import { getSession } from "@/lib/auth"
import { AppNav } from "@/components/AppNav"

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await getSession()
  if (!session) {
    redirect("/login")
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <AppNav session={session} />
      <main className="flex-1 overflow-auto bg-zinc-50 dark:bg-zinc-950">{children}</main>
    </div>
  )
}
