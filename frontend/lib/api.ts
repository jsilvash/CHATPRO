"use client"

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

async function refreshTokens(): Promise<boolean> {
  const res = await fetch("/api/auth/refresh", { method: "POST" })
  return res.ok
}

export async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {},
  retry = true,
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
  })

  if (res.status === 401 && retry) {
    const refreshed = await refreshTokens()
    if (refreshed) {
      return apiFetch<T>(path, options, false)
    }
    window.location.href = "/login"
    throw new ApiError(401, "Sesión expirada")
  }

  if (!res.ok) {
    const body = await res.text()
    throw new ApiError(res.status, body)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export async function apiGet<T>(path: string, params?: Record<string, string | number | undefined>) {
  const url = new URL(`${API_URL}${path}`)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined) url.searchParams.set(k, String(v))
    })
  }
  const res = await fetch(url.toString(), {
    credentials: "include",
  })

  if (res.status === 401) {
    const refreshed = await refreshTokens()
    if (refreshed) {
      const retry = await fetch(url.toString(), { credentials: "include" })
      if (!retry.ok) throw new ApiError(retry.status, await retry.text())
      if (retry.status === 204) return undefined as T
      return retry.json() as Promise<T>
    }
    window.location.href = "/login"
    throw new ApiError(401, "Sesión expirada")
  }

  if (!res.ok) throw new ApiError(res.status, await res.text())
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export { API_URL, ApiError }
