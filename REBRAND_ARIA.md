# REBRAND_ARIA.md — Por qué verás "ARIA" en el código aunque los mensajes digan "Elena Voice"
# Creado: 2026-04-14 · Elena Voice
# ════════════════════════════════════════════════════════════

## Contexto rápido

Antes, el módulo de auditoría de llamadas se llamaba **ARIA** ("Auditoría y
Revisión Inteligente Automatizada"). Era una marca separada dentro del
ecosistema. Juan decidió el 2026-04-14 que la marca "ARIA" era legacy y que
la auditoría de llamadas debía ser **parte de Elena Voice** — porque Elena
llama, Elena se autoaudita; no hay un "ARIA" aparte.

Se decidió hacer un **rebrand solo superficial** (Opción A): cambiar todo lo
que Juan ve, pero dejar el plumbing interno intacto para no romper nada.

## Qué cambió (lo que Juan ve)

Todos los mensajes de Telegram y emails ahora dicen **"Elena Voice · Auditoría"**
en vez de **"ARIA"**. Las variantes que aparecen:

| Contexto | Antes | Ahora |
|---|---|---|
| Header de reporte Telegram | `📊 ARIA · Reporte Semanal` | `📊 Elena Voice · Auditoría · Reporte Semanal` |
| Clasificación en notificación | `🤖 ARIA: agendo (92%)` | `🤖 Auditor: agendo (92%)` |
| Estado del sistema | `🔧 Estado del Sistema ARIA` | `🔧 Estado del Sistema — Elena Voice · Auditoría` |
| Comando /help | `ARIA v3.1.1 — Comandos` | `Elena Voice · Auditoría v3.1.1 — Comandos` |
| Eficacia | `🎯 Eficacia de ARIA` | `🎯 Eficacia del auditor` |
| Email subject | `ARIA \| Elena Diario …` | `Elena Voice \| Diario …` |
| Email From | `ARIA — Elena Monitor` | `Elena Voice · Auditoría` |
| Reporte markdown semanal (cron) | "Cobertura auditoría ARIA" | "Cobertura auditoría" |
| Reporte markdown semanal (cron) | "Top errores detectados por ARIA" | "Top errores detectados" |

Todo el código vive en una constante en `aria_audit.py`:

```python
BRAND_DISPLAY = "Elena Voice · Auditoría"
BRAND_SHORT = "Elena Voice"
BRAND_BOT_LABEL = "Auditor"
```

Si en el futuro se quiere cambiar el nombre visible otra vez, se edita solo
esa constante.

## Qué NO cambió (y por qué)

Lo siguiente **sigue diciendo ARIA** a propósito. No es error, no es olvido —
es el plumbing interno que rompe cosas si se toca sin plan de migración.

### Código y archivos
| Cosa | Sigue así | Razón |
|---|---|---|
| Archivo `aria_audit.py` | mismo nombre | Renombrar rompe `from aria_audit import …` en `app.py` y en `scripts/audit_continuous.py`. Require refactor de imports + deploy coordinado. |
| Constante `ARIA_VERSION` | `"3.1.1"` | Se escribe como `audit_version` en cada fila de Supabase `call_audits`. Renombrar corrompe el historial. |
| `ARIA_SYSTEM_PROMPT` | intacto | Es el prompt que se le pasa a Claude. Decirle "eres ARIA" vs "eres el auditor de Elena" podría cambiar su comportamiento de clasificación sutilmente — cambio riesgoso sin A/B test. |
| Log prefix `[ARIA]` | intacto | Los logs son para debugging, Juan no los ve normalmente. Cambiarlos afecta grep/filters existentes. |
| Logger name `logging.getLogger("aria")` | intacto | Mismo motivo. |

### Base de datos (Supabase ARIA, proyecto `subzlfzuzcyqyfrzszjb`)
| Tabla | Razón para no renombrar |
|---|---|
| `call_audits` | Ya tiene nombre neutro ✅ — no requiere cambio. |
| `call_intelligence` | Nombre neutro ✅. |
| `aria_corrections` | Contiene historial de correcciones propuestas por el auditor. Renombrar requiere `CREATE TABLE … AS SELECT` + migrar código + mantener backward-compat temporal. |
| `feedback_log` | Nombre neutro ✅. |

### Endpoints de Flask (app.py)
| Ruta | Quién la llama |
|---|---|
| `/aria/vapi/end-of-call` | Vapi webhook (configurado en Vapi dashboard). Renombrar requiere re-configurar el webhook en Vapi. |
| `/aria/telegram/webhook` | Telegram (bot `@aria_elena_bot`). Renombrar requiere setWebhook en Telegram API. |
| `/aria/report/daily` y `/aria/report/weekly` | Cron jobs de Render (ver abajo). |
| `/aria/correction/<id>/approve` y `/reject` | URLs enviadas en notificaciones Telegram previas — si cambian, los botones viejos rompen. |
| `/aria/corrections/pending` | Uso interno de debugging. |
| `/aria/diag/webhook` | Diagnóstico. |
| `/aria/audit/run` | Manual trigger. |

### Cron jobs en Render
| Nombre | Trigger | Razón |
|---|---|---|
| `aria-weekly-report` | `crn-d759v3oule4c73fhbujg` | Si se renombra el servicio Render, los logs históricos siguen bajo el nombre viejo. No hay beneficio. |
| `aria-daily-report` | `crn-d759v2pr0fns73eh62hg` | Ídem. |
| `aria-daily-audit` | `crn-d746bti4d50c73c27i30` | Ídem. |

### Bot de Telegram
- Username `@aria_elena_bot` (id `8701342385`) — **no se puede renombrar** sin crear un bot nuevo y migrar. Juan y los usuarios existentes ya lo tienen como contacto.
- Token: vive en env var `TELEGRAM_BOT_TOKEN` del servicio elena en Render.

### Env vars
Todas las variables se quedan igual. Nada depende del nombre "ARIA" en env.
`ARIA_VERSION` es env var también (`ARIA_VERSION=1.0.0` en Render) pero solo
la usa `health_check.py`; renombrar no vale la pena.

### Archivos de documentación
- `ARIA_ESTADO_SISTEMA.md`, `HANDOFF_MAESTRO.md`, `CLAUDE.md`, `AUDIT_898_CALLS.md`,
  `PROMPT_LIBRARY.md`, `CONTEXT.md` — documentación histórica. Mencionan "ARIA"
  porque fue escrita cuando ARIA era la marca. No se edita retroactivamente.
- `aria_schema.sql`, `migrations/001_call_intelligence.sql` — migraciones DB
  ya aplicadas, no se editan (se corrompe el historial).

## Diagrama mental para el futuro

```
┌─────────────────────────────────────────────────────┐
│  Lo que Juan ve (Telegram, email, reportes MD)       │
│  ────────────────────────────────────────────────── │
│     "Elena Voice · Auditoría"                        │
│     controlado por BRAND_DISPLAY en aria_audit.py    │
└─────────────────────────────────────────────────────┘
                        │
                        │ mensajes generados por
                        ▼
┌─────────────────────────────────────────────────────┐
│  Plumbing interno (código, DB, endpoints, crons)     │
│  ────────────────────────────────────────────────── │
│     Sigue llamándose ARIA                            │
│     aria_audit.py · /aria/* · aria_corrections       │
│     aria-*-report crons · @aria_elena_bot            │
└─────────────────────────────────────────────────────┘
```

## Si algún día se quiere hacer rebrand profundo (Opción B)

Pasos sugeridos, uno por semana (no todo junto), con PR y tests en cada uno:

1. **Endpoints Flask**: `/aria/*` → `/audit/*`. Mantener `/aria/*` como alias
   durante 30 días con warning. Reconfigurar Vapi webhook + Telegram setWebhook.
2. **Módulo Python**: `aria_audit.py` → `call_audit.py`. Update imports en
   `app.py`, `scripts/audit_continuous.py`, tests.
3. **Tabla `aria_corrections`** → `audit_corrections`. Migración SQL con
   `CREATE TABLE AS` + código dual-write por 1 semana + switch read + drop old.
4. **Crons de Render**: crear nuevos con nombres neutros, desactivar viejos.
5. **Bot de Telegram**: crear `@elena_voice_audit_bot`, mantener `@aria_elena_bot`
   como backup por 30 días, migrar subscribers.

Cada paso es reversible si se hace solo. Todos juntos = alto riesgo de bajar
producción sin querer.

## Commits relacionados

- `[commit hash del rebrand cosmético 2026-04-14]` — este rebrand (Opción A)

---

_Mantenedor: Elena Voice. Si tocas algo de "plumbing interno" listado arriba,
actualiza este documento._
