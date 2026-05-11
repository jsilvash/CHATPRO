import Link from "next/link"
import { Home, MessageSquare } from "lucide-react"

export default function NotFound() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-zinc-50 dark:bg-zinc-950 px-4">
      <div className="text-center max-w-md">
        <p className="text-8xl font-bold text-zinc-200 dark:text-zinc-800 select-none" aria-hidden="true">
          404
        </p>
        <h1 className="mt-4 text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
          Página no encontrada
        </h1>
        <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
          La página que buscas no existe o fue movida.
        </p>
        <div className="mt-8 flex items-center justify-center gap-3">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-zinc-900 text-white text-sm font-medium hover:bg-zinc-700 dark:bg-zinc-50 dark:text-zinc-900 dark:hover:bg-zinc-200 transition-colors"
          >
            <Home className="w-4 h-4" />
            Ir al dashboard
          </Link>
          <Link
            href="/inbox"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md border border-zinc-200 dark:border-zinc-700 text-sm font-medium text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
          >
            <MessageSquare className="w-4 h-4" />
            Ver inbox
          </Link>
        </div>
      </div>
    </div>
  )
}
