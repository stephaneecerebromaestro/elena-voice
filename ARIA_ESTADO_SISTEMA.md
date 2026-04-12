> ⚠️ **ARCHIVO HISTÓRICO** — Este documento NO refleja el estado actual del sistema. El documento vigente es `CLAUDE.md`. Se conserva como referencia del camino recorrido.

---

# ARIA + Elena — Estado del Sistema y Plan Estratégico
**Última actualización:** 30 marzo 2026

---

## Estado actual del sistema

### Infraestructura
- **Servidor:** Render (elena-pdem.onrender.com) — `live` v17.46
- **Base de datos:** Supabase (`subzlfzuzcyqyfrzszjb.supabase.co`)
- **Repositorio:** GitHub (`stephaneecerebromaestro/elena`) — rama `main`
- **Vapi Assistant ID:** `1631c7cf-2914-45f9-bf82-6635cdf00aba`
- **Phone Twilio (inbound):** `+17869835076` — `e8b9ed6d` — serverUrl configurado
- **Phone Vapi nativo:** `+17867430129`

### ARIA — Componentes activos
| Componente | Estado | Notas |
|-----------|--------|-------|
| Polling activo | ✅ Live | Cada 3 min, cubre inbound Twilio |
| Webhook end-of-call | ✅ Live | Para outbound cuando Vapi lo envía |
| Notificación Telegram | ✅ Live | 3 niveles: 🟢/🟡/🔴 con nombre + hora EDT |
| Reporte diario 8PM EDT | ✅ Configurado | Cron en app.py |
| Reporte semanal domingo 8AM | ✅ Configurado | Cron en app.py |
| Comandos Telegram | ✅ Live | /score, /audit, /reporte, /errores, /eficacia, /llamada |
| Feedback loop (aprobar/rechazar) | ✅ Live | Guarda en feedback_log + aria_corrections |
| Aprendizaje few-shot | ❌ No implementado | Prioridad 2 del plan |

### Métricas reales (85 audits, 100 llamadas)
| Métrica | Valor |
|---------|-------|
| Tasa de contestación | 45% (45/100) |
| Tasa de conversión (conversaciones → citas) | **6.7%** (3/45) |
| Duración promedio con conversación | 78s |
| Playbook score promedio en no_agendo | **30%** |
| Confianza promedio de ARIA | 98% |
| Discrepancias detectadas | 10 (8 resueltas) |

### Errores HIGH más frecuentes en Elena
| Error | Frecuencia | Impacto en conversión |
|-------|-----------|----------------------|
| premature_endcall | 12 | Alto — pierde leads activos |
| missed_close | 10 | Alto — no toma oportunidades |
| playbook_violation | 7 | Medio — flujo incorrecto |
| confusion_created | 5 | Medio — confunde al cliente |
| language_switch | 2 | Bajo — inglés/español |

---

## Plan estratégico priorizado

### Prioridad 1 — Mejorar prompt de Elena con errores reales ← EN CURSO
- **Objetivo:** Subir conversión de 6.7% a 12-15%
- **Método:** Leer los 11 transcripts no_agendo, identificar patrones, reescribir instrucciones
- **Riesgo:** Bajo — cambio de prompt, no de código
- **Métrica de éxito:** Playbook score no_agendo > 60% en próximas 50 llamadas

### Prioridad 2 — Few-shot dinámico en ARIA (aprendizaje nivel 1)
- **Objetivo:** ARIA calibra con feedback real de Juan
- **Método:** Inyectar últimos 10 feedbacks aprobados/rechazados en prompt de Claude antes de cada audit
- **Riesgo:** Bajo — solo modifica el user_prompt en audit_call_with_claude()
- **Métrica de éxito:** Discrepancias < 2/semana

### Prioridad 3 — Loop de mejora continua de Elena
- **Objetivo:** Elena mejora automáticamente semana a semana
- **Método:** Si mismo error HIGH > 3 veces/semana → ARIA propone corrección al prompt → Juan aprueba → se aplica
- **Riesgo:** Medio — requiere lógica de diff de prompts
- **Métrica de éxito:** Reducción del 20% en errores HIGH por mes

### Prioridad 4 — Seguimiento post-llamada no_agendo
- **Objetivo:** Recuperar 10-20% de leads que no agendaron
- **Método:** Workflow WhatsApp/SMS 2h después de no_agendo
- **Riesgo:** Bajo — externo al sistema actual
- **Métrica de éxito:** 10% de no_agendo convierten en cita en 48h

---

## Historial de fixes aplicados (30 marzo 2026)
1. `import logging` faltante en app.py → NameError en webhook
2. `_to_bool()` para elena_success_eval → invalid input syntax for boolean
3. `status = "ended"` inyectado en process_single_call_realtime → webhook no incluía status
4. `telegram_notify_call()` → monitoreo total (🟢/🟡/🔴) reemplaza solo-discrepancias
5. `serverUrl` agregado al phone number Twilio en Vapi → inbound no disparaba webhook
6. Polling activo cada 3 min → solución definitiva para inbound Twilio

---

## Archivos clave
- `app.py` — Servidor Flask principal, Elena tool server, webhooks Vapi, cron ARIA
- `aria_audit.py` — Motor de auditoría ARIA, Claude, Telegram, Supabase, polling
- `ARIA_ESTADO_SISTEMA.md` — Este archivo
