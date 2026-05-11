export default function Loading() {
  return (
    <div className="p-6 space-y-4 animate-pulse" role="status" aria-label="Cargando contenido">
      {/* Header skeleton */}
      <div className="h-8 bg-zinc-200 dark:bg-zinc-800 rounded w-48" />

      {/* Content card skeletons */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-white dark:bg-zinc-900 rounded-xl border border-zinc-200 dark:border-zinc-800 p-4 space-y-3">
            <div className="h-4 bg-zinc-200 dark:bg-zinc-800 rounded w-3/4" />
            <div className="h-8 bg-zinc-200 dark:bg-zinc-800 rounded w-1/2" />
          </div>
        ))}
      </div>

      {/* Table skeleton */}
      <div className="bg-white dark:bg-zinc-900 rounded-xl border border-zinc-200 dark:border-zinc-800 overflow-hidden">
        <div className="p-4 border-b border-zinc-200 dark:border-zinc-800">
          <div className="h-4 bg-zinc-200 dark:bg-zinc-800 rounded w-32" />
        </div>
        <div className="divide-y divide-zinc-200 dark:divide-zinc-800">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-3">
              <div className="w-8 h-8 bg-zinc-200 dark:bg-zinc-800 rounded-full shrink-0" />
              <div className="flex-1 space-y-1.5">
                <div className="h-3.5 bg-zinc-200 dark:bg-zinc-800 rounded w-1/3" />
                <div className="h-3 bg-zinc-200 dark:bg-zinc-800 rounded w-1/2" />
              </div>
              <div className="h-6 bg-zinc-200 dark:bg-zinc-800 rounded w-16 shrink-0" />
            </div>
          ))}
        </div>
      </div>

      <span className="sr-only">Cargando...</span>
    </div>
  )
}
