# ADR-0002: Enrutado con un modelo de lenguaje (TypeSafe Jev)

Estado: **aceptada** (2026-10-20, petición de Santi).

## Decisión
El texto ya transcrito pasa por un modelo de lenguaje que decide, en **una sola petición** a TypeSafe System One (`jev-latest`):
- **intención:** trabajar en un proyecto, continuar, crear un proyecto, anotar una idea, consulta de estado o ininteligible;
- **proyecto:** entre los proyectos registrados;
- **máquina:** entre los runners (VPS, PC, Mac);
- **agente de código:** entre los agentes de esa máquina en T3 (Claude, Codex, Cursor…).

Cada elección viene con su probabilidad y unas preguntas Noul indican si el dictado *menciona* la máquina o el agente. Si no los menciona, se usan los valores por defecto.

- Jev **selecciona, no genera**. El prompt que recibe el agente es el dictado **literal**, y el nombre de un proyecto nuevo se extrae del propio dictado.
- **Umbrales:** confianza ≥ 0,75 y Noul ≥ 0,5. Son provisionales y hay que calibrarlos con unos 100 dictados etiquetados. Por debajo, se pide aclaración con los dos candidatos más probables.
- **Lo que el modelo nunca decide:** la política (bypass, aprobaciones, denegaciones). Las frases de política se resuelven antes, con la gramática determinista, y no llegan a Jev.
- **Fallo de Jev** (error, red, falta de clave): se usan las reglas deterministas, que son conservadoras.
- **Otros modelos:** se enchufan implementando el mismo `decide()`, como alternativa o cascada. No se ha implementado ninguno más: el proveedor "Laya" no está identificado.
- **Pista del reloj:** `runner_hint` tiene prioridad sobre la máquina que elija el modelo.

## Configuración
- Variables de entorno: `TYPESAFE_API_KEY` y `TYPESAFE_MODEL` (por defecto `jev-latest`).
- Agentes por máquina: `app.admin runner-add … --agent claude=claudeAgent:claude-opus-5-5:Claude --agent codex=codex:gpt-6.1-sol:Codex`. El primero es el agente por defecto.
- Valores reales de este PC, según `orchestrator_capabilities`: `codex`, `claudeAgent`, `cursor`, `grok`, `opencode`, `pi`.

## Verificado
- 7 tests con una API TypeSafe falsa, con la forma de respuesta de docs.typesafe.ai/api, sobre SQLite y PostgreSQL 16.
- **No probado contra la API real:** no hay clave en este entorno.
