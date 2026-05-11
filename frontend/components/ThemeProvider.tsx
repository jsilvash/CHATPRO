"use client"

import { createContext, useContext, useEffect, useState } from "react"

type Theme = "light" | "dark" | "system"

interface ThemeContextValue {
  theme: Theme
  setTheme: (t: Theme) => void
}

const ThemeCtx = createContext<ThemeContextValue>({ theme: "system", setTheme: () => {} })

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("system")

  // Leer preferencia almacenada al montar
  useEffect(() => {
    const stored = localStorage.getItem("chatpro-theme") as Theme | null
    if (stored === "light" || stored === "dark" || stored === "system") {
      setThemeState(stored)
    }
  }, [])

  // Aplicar clase .dark al elemento <html>
  useEffect(() => {
    const root = document.documentElement

    if (theme === "dark") {
      root.classList.add("dark")
      localStorage.setItem("chatpro-theme", "dark")
      return
    }

    if (theme === "light") {
      root.classList.remove("dark")
      localStorage.setItem("chatpro-theme", "light")
      return
    }

    // "system": detectar preferencia del SO y escuchar cambios
    const mq = window.matchMedia("(prefers-color-scheme: dark)")
    const apply = () => root.classList.toggle("dark", mq.matches)
    apply()
    mq.addEventListener("change", apply)
    localStorage.setItem("chatpro-theme", "system")
    return () => mq.removeEventListener("change", apply)
  }, [theme])

  function setTheme(t: Theme) {
    setThemeState(t)
  }

  return <ThemeCtx.Provider value={{ theme, setTheme }}>{children}</ThemeCtx.Provider>
}

export function useTheme() {
  return useContext(ThemeCtx)
}
