import { NextRequest, NextResponse } from "next/server"
import { cookies } from "next/headers"
import { setAuthCookies } from "@/lib/auth"

const API_URL = process.env.API_URL ?? "http://localhost:8000"

export async function POST(request: NextRequest) {
  const body = await request.json()

  let backendRes: Response
  try {
    backendRes = await fetch(`${API_URL}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: body.email,
        password: body.password,
      }),
    })
  } catch {
    return NextResponse.json(
      { detail: "No se puede conectar con el servidor. Intenta de nuevo." },
      { status: 503 },
    )
  }

  if (!backendRes.ok) {
    const error = await backendRes.json().catch(() => ({ detail: "Email o contraseña incorrectos" }))
    return NextResponse.json(error, { status: backendRes.status })
  }

  const data = await backendRes.json()
  const { access_token, refresh_token } = data

  const cookieStore = await cookies()
  setAuthCookies(cookieStore, access_token, refresh_token)

  return NextResponse.json({ ok: true })
}
