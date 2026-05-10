import { NextResponse } from "next/server"
import { cookies } from "next/headers"
import { getRefreshToken, setAuthCookies } from "@/lib/auth"

const API_URL = process.env.API_URL ?? "http://localhost:8000"

export async function POST() {
  const refreshToken = await getRefreshToken()
  if (!refreshToken) {
    return NextResponse.json({ detail: "Sin refresh token" }, { status: 401 })
  }

  const backendRes = await fetch(`${API_URL}/v1/auth/refresh`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${refreshToken}`,
    },
  })

  if (!backendRes.ok) {
    return NextResponse.json({ detail: "Refresh fallido" }, { status: 401 })
  }

  const data = await backendRes.json()
  const { access_token, refresh_token } = data

  const cookieStore = await cookies()
  setAuthCookies(cookieStore, access_token, refresh_token)

  return NextResponse.json({ ok: true })
}
