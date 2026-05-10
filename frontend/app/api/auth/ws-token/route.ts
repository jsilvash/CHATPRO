import { NextResponse } from "next/server"
import { getAccessToken } from "@/lib/auth"

export async function GET() {
  const token = await getAccessToken()
  if (!token) {
    return NextResponse.json({ detail: "No autenticado" }, { status: 401 })
  }
  // Devuelve el access token en el body para uso en WebSocket handshake
  return NextResponse.json({ token })
}
