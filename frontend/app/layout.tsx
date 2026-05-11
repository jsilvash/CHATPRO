import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { SkipToContent } from "@/components/SkipToContent";

export const metadata: Metadata = {
  title: "ChatPro",
  description: "Panel de gestión de WhatsApp Hub",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es" className="h-full antialiased" suppressHydrationWarning>
      <body className="min-h-full flex flex-col">
        <SkipToContent />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
