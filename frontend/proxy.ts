import { NextResponse } from "next/server"
import type { NextRequest } from "next/server"

const COOKIE_NAME = process.env.COOKIE_NAME ?? "chatpro_access"

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl
  const token = request.cookies.get(COOKIE_NAME)?.value

  // Rutas públicas que no requieren autenticación
  const isPublic =
    pathname.startsWith("/login") ||
    pathname.startsWith("/api/auth") ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon")

  if (isPublic) {
    // Si ya está autenticado y trata de ir al login, redirigir al inbox
    if (pathname.startsWith("/login") && token) {
      return NextResponse.redirect(new URL("/inbox", request.url))
    }
    return NextResponse.next()
  }

  // Ruta protegida sin token → redirigir al login
  if (!token) {
    const loginUrl = new URL("/login", request.url)
    loginUrl.searchParams.set("next", pathname)
    return NextResponse.redirect(loginUrl)
  }

  return NextResponse.next()
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
}
