import { cookies } from "next/headers"
import { jwtVerify, decodeJwt } from "jose"

const COOKIE_NAME = process.env.COOKIE_NAME ?? "chatpro_access"
const REFRESH_COOKIE_NAME = process.env.REFRESH_COOKIE_NAME ?? "chatpro_refresh"

export interface JWTPayload {
  sub: string
  tenant_id: string
  role: string
  email?: string
  exp?: number
  iat?: number
}

export async function getAccessToken(): Promise<string | undefined> {
  const cookieStore = await cookies()
  return cookieStore.get(COOKIE_NAME)?.value
}

export async function getRefreshToken(): Promise<string | undefined> {
  const cookieStore = await cookies()
  return cookieStore.get(REFRESH_COOKIE_NAME)?.value
}

export async function getSession(): Promise<JWTPayload | null> {
  const token = await getAccessToken()
  if (!token) return null
  try {
    const payload = decodeJwt(token) as JWTPayload
    return payload
  } catch {
    return null
  }
}

export function setAuthCookies(
  cookieStore: Awaited<ReturnType<typeof cookies>>,
  accessToken: string,
  refreshToken: string,
) {
  cookieStore.set(COOKIE_NAME, accessToken, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60, // 1 hora
  })
  cookieStore.set(REFRESH_COOKIE_NAME, refreshToken, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 7, // 7 días
  })
}

export function clearAuthCookies(cookieStore: Awaited<ReturnType<typeof cookies>>) {
  cookieStore.delete(COOKIE_NAME)
  cookieStore.delete(REFRESH_COOKIE_NAME)
}

export { COOKIE_NAME, REFRESH_COOKIE_NAME }
