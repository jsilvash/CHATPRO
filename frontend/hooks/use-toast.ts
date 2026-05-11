"use client"

import { create } from "zustand"

export type ToastVariant = "default" | "success" | "destructive"

export interface ToastItem {
  id: string
  title: string
  description?: string
  variant?: ToastVariant
}

interface ToastStore {
  toasts: ToastItem[]
  addToast: (t: Omit<ToastItem, "id">) => void
  removeToast: (id: string) => void
}

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  addToast: (t) => {
    const id = Math.random().toString(36).slice(2, 9)
    set((s) => ({ toasts: [...s.toasts, { ...t, id }] }))
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) }))
    }, 4000)
  },
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
}))

export function useToast() {
  const { addToast } = useToastStore()
  return {
    toast: (opts: Omit<ToastItem, "id">) => addToast(opts),
    success: (title: string, description?: string) =>
      addToast({ title, description, variant: "success" }),
    error: (title: string, description?: string) =>
      addToast({ title, description, variant: "destructive" }),
    info: (title: string, description?: string) =>
      addToast({ title, description, variant: "default" }),
  }
}
