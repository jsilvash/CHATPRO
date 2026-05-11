"use client"

import { useEffect, useState, useCallback } from "react"
import { apiGet, apiFetch } from "@/lib/api"
import type { OfficeHoursOut, WaNumberListResponse } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Clock, Plus, Trash2, Pencil, Check, X, AlertCircle, Clock3, LayoutGrid, List } from "lucide-react"

const DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

function pad(n: number) {
  return String(n).padStart(2, "0")
}

interface FormState {
  wa_number_id: string
  day_of_week: number
  hour_start: number
  hour_end: number
  is_active: boolean
  out_of_hours_message: string
}

const EMPTY_FORM: FormState = {
  wa_number_id: "",
  day_of_week: 0,
  hour_start: 9,
  hour_end: 18,
  is_active: true,
  out_of_hours_message: "",
}

interface OfficeHoursRowProps {
  record: OfficeHoursOut
  waNumbers: WaNumberListResponse["items"]
  onDelete: (id: string) => void
  onUpdate: (id: string, data: Partial<FormState>) => Promise<void>
}

function OfficeHoursRow({ record, waNumbers, onDelete, onUpdate }: OfficeHoursRowProps) {
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<FormState>({
    wa_number_id: record.wa_number_id ?? "",
    day_of_week: record.day_of_week,
    hour_start: record.hour_start,
    hour_end: record.hour_end,
    is_active: record.is_active,
    out_of_hours_message: record.out_of_hours_message,
  })
  const [saving, setSaving] = useState(false)

  const waLabel = waNumbers.find(w => w.id === record.wa_number_id)?.label ?? "Global"

  async function handleSave() {
    setSaving(true)
    try {
      await onUpdate(record.id, form)
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <tr className="border-b border-zinc-100 dark:border-zinc-800">
        <td className="py-2 px-3">
          <select
            value={form.day_of_week}
            onChange={e => setForm(f => ({ ...f, day_of_week: +e.target.value }))}
            className="h-7 rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-xs px-2"
          >
            {DAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
          </select>
        </td>
        <td className="py-2 px-3">
          <select
            value={form.wa_number_id}
            onChange={e => setForm(f => ({ ...f, wa_number_id: e.target.value }))}
            className="h-7 rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-xs px-2"
          >
            <option value="">Global</option>
            {waNumbers.map(w => <option key={w.id} value={w.id}>{w.label}</option>)}
          </select>
        </td>
        <td className="py-2 px-3">
          <div className="flex items-center gap-1">
            <Input
              type="number"
              min={0}
              max={23}
              value={form.hour_start}
              onChange={e => setForm(f => ({ ...f, hour_start: +e.target.value }))}
              className="h-7 w-16 text-xs"
            />
            <span className="text-xs text-zinc-400">–</span>
            <Input
              type="number"
              min={0}
              max={23}
              value={form.hour_end}
              onChange={e => setForm(f => ({ ...f, hour_end: +e.target.value }))}
              className="h-7 w-16 text-xs"
            />
          </div>
        </td>
        <td className="py-2 px-3">
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))}
            className="h-4 w-4"
          />
        </td>
        <td className="py-2 px-3">
          <Input
            value={form.out_of_hours_message}
            onChange={e => setForm(f => ({ ...f, out_of_hours_message: e.target.value }))}
            placeholder="Mensaje fuera de horario…"
            className="h-7 text-xs"
          />
        </td>
        <td className="py-2 px-3">
          <div className="flex gap-1">
            <Button size="icon" variant="ghost" onClick={handleSave} disabled={saving} className="h-6 w-6">
              <Check className="w-3 h-3 text-green-600" />
            </Button>
            <Button size="icon" variant="ghost" onClick={() => setEditing(false)} className="h-6 w-6">
              <X className="w-3 h-3 text-zinc-400" />
            </Button>
          </div>
        </td>
      </tr>
    )
  }

  return (
    <tr className="border-b border-zinc-100 dark:border-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-800/50">
      <td className="py-2.5 px-3 text-sm text-zinc-700 dark:text-zinc-300">{DAYS[record.day_of_week]}</td>
      <td className="py-2.5 px-3 text-sm text-zinc-500">
        <span className={record.wa_number_id ? "text-zinc-700 dark:text-zinc-300" : "italic text-zinc-400"}>
          {waLabel}
        </span>
      </td>
      <td className="py-2.5 px-3 text-sm font-mono text-zinc-700 dark:text-zinc-300">
        {pad(record.hour_start)}:00 – {pad(record.hour_end)}:00
      </td>
      <td className="py-2.5 px-3">
        <Badge variant={record.is_active ? "success" : "secondary"} className="text-xs">
          {record.is_active ? "Activo" : "Inactivo"}
        </Badge>
      </td>
      <td className="py-2.5 px-3 text-xs text-zinc-500 max-w-[200px] truncate">
        {record.out_of_hours_message || <span className="italic text-zinc-400">Sin mensaje</span>}
      </td>
      <td className="py-2.5 px-3">
        <div className="flex gap-1">
          <Button size="icon" variant="ghost" onClick={() => setEditing(true)} className="h-6 w-6">
            <Pencil className="w-3 h-3 text-zinc-400" />
          </Button>
          <Button size="icon" variant="ghost" onClick={() => onDelete(record.id)} className="h-6 w-6">
            <Trash2 className="w-3 h-3 text-zinc-400 hover:text-red-500" />
          </Button>
        </div>
      </td>
    </tr>
  )
}

export default function OfficeHoursPage() {
  const [records, setRecords] = useState<OfficeHoursOut[]>([])
  const [waNumbers, setWaNumbers] = useState<WaNumberListResponse["items"]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [recs, wns] = await Promise.all([
        apiGet<OfficeHoursOut[]>("/v1/office-hours"),
        apiGet<WaNumberListResponse>("/v1/wa-numbers"),
      ])
      setRecords(recs)
      setWaNumbers(wns.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function handleCreate() {
    setSaving(true)
    try {
      const body: Record<string, unknown> = {
        day_of_week: form.day_of_week,
        hour_start: form.hour_start,
        hour_end: form.hour_end,
        is_active: form.is_active,
        out_of_hours_message: form.out_of_hours_message,
      }
      if (form.wa_number_id) body.wa_number_id = form.wa_number_id
      await apiFetch("/v1/office-hours", { method: "POST", body: JSON.stringify(body) })
      setForm(EMPTY_FORM)
      setCreating(false)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al crear")
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("¿Eliminar este horario?")) return
    try {
      await apiFetch(`/v1/office-hours/${id}`, { method: "DELETE" })
      setRecords(prev => prev.filter(r => r.id !== id))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al eliminar")
    }
  }

  async function handleUpdate(id: string, data: Partial<FormState>) {
    const body: Record<string, unknown> = { ...data }
    if (data.wa_number_id === "") body.wa_number_id = null
    await apiFetch(`/v1/office-hours/${id}`, { method: "PUT", body: JSON.stringify(body) })
    await load()
  }

  // Agrupar por día para mejor visualización
  const byDay = DAYS.map((_, day) => records.filter(r => r.day_of_week === day))
  const [gridView, setGridView] = useState(false)

  // Construir mapa de cobertura para el grid: day → set of covered hours
  const coverageMap: boolean[][] = DAYS.map((_, day) => {
    const dayRecs = records.filter(r => r.day_of_week === day && r.is_active)
    return Array.from({ length: 24 }, (_, h) =>
      dayRecs.some(r => h >= r.hour_start && h < r.hour_end)
    )
  })

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Clock3 className="w-5 h-5 text-zinc-500" />
          <div>
            <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Horarios de atención</h1>
            <p className="text-xs text-zinc-500 mt-0.5">
              Configura cuándo el bot atiende automáticamente (0=lunes, 6=domingo)
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border border-zinc-200 dark:border-zinc-700 overflow-hidden">
            <button
              onClick={() => setGridView(false)}
              className={`p-1.5 transition-colors ${!gridView ? "bg-zinc-100 dark:bg-zinc-800 text-zinc-900 dark:text-zinc-50" : "text-zinc-400 hover:text-zinc-600"}`}
              title="Vista lista"
            >
              <List className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setGridView(true)}
              className={`p-1.5 transition-colors ${gridView ? "bg-zinc-100 dark:bg-zinc-800 text-zinc-900 dark:text-zinc-50" : "text-zinc-400 hover:text-zinc-600"}`}
              title="Vista semanal"
            >
              <LayoutGrid className="w-3.5 h-3.5" />
            </button>
          </div>
          <Button onClick={() => setCreating(v => !v)} size="sm" className="gap-1.5">
            <Plus className="w-3.5 h-3.5" />
            Nuevo horario
          </Button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
          <button onClick={() => setError(null)} className="ml-auto">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Formulario de creación */}
      {creating && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Nuevo horario</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              <div>
                <Label className="text-xs">Día de la semana</Label>
                <select
                  value={form.day_of_week}
                  onChange={e => setForm(f => ({ ...f, day_of_week: +e.target.value }))}
                  className="mt-1 w-full h-9 rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm px-3 text-zinc-900 dark:text-zinc-50 focus:outline-none focus:ring-2 focus:ring-zinc-400"
                >
                  {DAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
                </select>
              </div>

              <div>
                <Label className="text-xs">Número WA (opcional)</Label>
                <select
                  value={form.wa_number_id}
                  onChange={e => setForm(f => ({ ...f, wa_number_id: e.target.value }))}
                  className="mt-1 w-full h-9 rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm px-3 text-zinc-900 dark:text-zinc-50 focus:outline-none focus:ring-2 focus:ring-zinc-400"
                >
                  <option value="">Global (todos los números)</option>
                  {waNumbers.map(w => <option key={w.id} value={w.id}>{w.label}</option>)}
                </select>
              </div>

              <div>
                <Label className="text-xs">Hora inicio – Hora fin</Label>
                <div className="mt-1 flex items-center gap-2">
                  <Input
                    type="number"
                    min={0}
                    max={23}
                    value={form.hour_start}
                    onChange={e => setForm(f => ({ ...f, hour_start: +e.target.value }))}
                    className="w-20"
                    placeholder="9"
                  />
                  <span className="text-zinc-400 text-sm">–</span>
                  <Input
                    type="number"
                    min={0}
                    max={23}
                    value={form.hour_end}
                    onChange={e => setForm(f => ({ ...f, hour_end: +e.target.value }))}
                    className="w-20"
                    placeholder="18"
                  />
                </div>
              </div>

              <div className="col-span-2 md:col-span-2">
                <Label className="text-xs">Mensaje fuera de horario</Label>
                <Input
                  className="mt-1"
                  placeholder="Ej: Gracias por escribir. Atendemos de lunes a viernes de 9 a 18h."
                  value={form.out_of_hours_message}
                  onChange={e => setForm(f => ({ ...f, out_of_hours_message: e.target.value }))}
                />
              </div>

              <div className="flex items-end gap-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={form.is_active}
                    onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))}
                    className="h-4 w-4"
                  />
                  <span className="text-sm text-zinc-700 dark:text-zinc-300">Activo</span>
                </label>
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-4">
              <Button
                variant="outline"
                size="sm"
                onClick={() => { setCreating(false); setForm(EMPTY_FORM) }}
              >
                Cancelar
              </Button>
              <Button size="sm" onClick={handleCreate} disabled={saving}>
                {saving ? "Guardando…" : "Crear horario"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Vista de horarios */}
      {loading ? (
        <div className="animate-pulse space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-10 bg-zinc-100 dark:bg-zinc-800 rounded" />
          ))}
        </div>
      ) : records.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <Clock className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mb-3" />
          <p className="text-sm text-zinc-500">No hay horarios configurados</p>
          <p className="text-xs text-zinc-400 mt-1">
            Sin horarios activos, el bot atiende siempre.
          </p>
        </div>
      ) : gridView ? (
        /* Vista cuadrícula semanal */
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
              Cobertura semanal (horarios activos)
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0 pb-4">
            <div className="overflow-x-auto px-4">
              <div className="flex gap-0 min-w-[700px]">
                {/* Columna de horas */}
                <div className="flex flex-col pt-7 shrink-0">
                  {Array.from({ length: 24 }, (_, h) => (
                    <div key={h} className="h-5 flex items-center pr-1.5">
                      <span className="text-[10px] text-zinc-400 w-7 text-right">{String(h).padStart(2, "0")}h</span>
                    </div>
                  ))}
                </div>
                {/* Columnas por día */}
                {DAYS.map((day, dayIdx) => (
                  <div key={dayIdx} className="flex-1 min-w-0">
                    <div className="h-7 flex items-center justify-center">
                      <span className="text-[11px] font-medium text-zinc-600 dark:text-zinc-400">{day.slice(0, 3)}</span>
                    </div>
                    <div className="flex flex-col gap-px">
                      {coverageMap[dayIdx].map((covered, h) => (
                        <div
                          key={h}
                          className={`h-5 mx-0.5 rounded-sm transition-colors ${
                            covered
                              ? "bg-green-400 dark:bg-green-600"
                              : "bg-zinc-100 dark:bg-zinc-800"
                          }`}
                          title={`${day} ${String(h).padStart(2, "0")}:00${covered ? " — Cubierto" : ""}`}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              {/* Leyenda */}
              <div className="flex items-center gap-4 mt-3 pl-9">
                <div className="flex items-center gap-1.5">
                  <div className="w-3 h-3 rounded-sm bg-green-400 dark:bg-green-600" />
                  <span className="text-xs text-zinc-500">Cubierto (bot activo)</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <div className="w-3 h-3 rounded-sm bg-zinc-100 dark:bg-zinc-800" />
                  <span className="text-xs text-zinc-500">Sin cobertura</span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : (
        /* Vista lista (tabla) */
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-zinc-100 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-800/50">
                    <th className="text-left py-2.5 px-3 text-xs font-medium text-zinc-500 uppercase tracking-wide">Día</th>
                    <th className="text-left py-2.5 px-3 text-xs font-medium text-zinc-500 uppercase tracking-wide">Número WA</th>
                    <th className="text-left py-2.5 px-3 text-xs font-medium text-zinc-500 uppercase tracking-wide">Horario</th>
                    <th className="text-left py-2.5 px-3 text-xs font-medium text-zinc-500 uppercase tracking-wide">Estado</th>
                    <th className="text-left py-2.5 px-3 text-xs font-medium text-zinc-500 uppercase tracking-wide">Mensaje</th>
                    <th className="py-2.5 px-3" />
                  </tr>
                </thead>
                <tbody>
                  {byDay.map((dayRecs, dayIdx) =>
                    dayRecs.map(rec => (
                      <OfficeHoursRow
                        key={rec.id}
                        record={rec}
                        waNumbers={waNumbers}
                        onDelete={handleDelete}
                        onUpdate={handleUpdate}
                      />
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      <p className="text-xs text-zinc-400">
        Los horarios específicos de un número tienen prioridad sobre los globales.
        Si no hay horarios activos, el bot atiende las 24h.
      </p>
    </div>
  )
}
