/**
 * i18n.ts — English / Spanish texts of the server-rendered pages (/dashboard,
 * /admin). The language comes from ?lang=en|es, else the browser's
 * Accept-Language. The chat UI (public/app.js) has its own dictionary.
 */

export type Lang = "en" | "es";

export function pickLang(req: Request): Lang {
  const q = new URL(req.url).searchParams.get("lang");
  if (q === "en" || q === "es") return q;
  return /^es\b/i.test(req.headers.get("Accept-Language") ?? "") ? "es" : "en";
}

const TEXTS = {
  en: {
    dashboardTitle: "LLMOps — live dashboard",
    dashboardIntro:
      "A live LLM agent on Cloudflare's free tier, with the operations around it: cost per call and budgets (FinOps), " +
      "an eval suite that gates every deploy plus an LLM judge (scoring), charter and content guardrails, CI/CD and " +
      "security checks. Everything below is computed live from the platform's own ledger.",
    askAgent: "Ask the agent", source: "Source code", architecture: "Architecture",
    readOnly: "Read-only view. Run details, human review and the agent switches are in the password-protected admin.",
    today: "Today (UTC)", last7: "Last 7 days",
    calls: "LLM calls", billed: "Billed today (beyond the free tier)", billedNote: "today's usage fits in the free allocation",
    billedOver: "usage went beyond the free allocation",
    value: "Usage at list price", valueNote: "neurons × $0.011 / 1,000 — what it would cost without the free tier",
    freeUsed: "Free allocation used by this platform", neuronsOf: "of", neurons: "neurons", errorRate: "Error rate",
    runs: "Runs", successRate: "Success rate", avgCost: "Avg value per run", judge: "Judge score",
    agreement: "Judge / human agreement", labeled: "labeled",
    valuePerDay: "Usage per day (list price)", callsPerDay: "Calls per day", errorsPerDay: "Errors per day",
    judgePerDay: "Judge score per day", max: "max", noData: "No data yet.",
    guardrails: "Guardrails (7 days)", rule: "Rule", action: "Action", count: "Count", none: "None.",
    releases: "Releases", deployed: "Deployed (UTC)", version: "Version", commit: "Commit", noReleases: "No deployments recorded.",
    agents: "Agents", agent: "Agent", status: "Status", guardrailMode: "Guardrails", budgetToday: "Budget used today",
    actions: "Actions", purpose: "Purpose", of: "of",
    evalByRelease: "Live evaluation by release", evalNote: "the deploy gate: fixed questions, deterministic grading",
    evalRuns: "Eval runs", passRate: "Pass rate", avgPerTask: "Avg value per task", noEval: "No evaluation runs yet.",
    latestEval: "Latest evaluation runs", when: "When (UTC)", task: "Task", question: "Question", result: "Result",
    cost: "Value", pass: "✓ pass", fail: "✗ fail",
    adminTitle: "LLMOps admin", publicView: "Public read-only view",
    adminNote: "Per-request logs, tokens and cost are also in the Cloudflare dashboard → AI → AI Gateway →",
    adminNote2: "Worker errors and latency in Workers → Observability.",
    review: "Review queue", reviewNote: "judged below", reviewNote2: "not labeled yet", nothingToReview: "Nothing to review.",
    run: "Run", label: "Label", recent: "Recent runs", sourceCol: "Source", steps: "Steps", noRuns: "No runs yet.",
  },
  es: {
    dashboardTitle: "LLMOps — panel en directo",
    dashboardIntro:
      "Un agente LLM en producción en el plan gratuito de Cloudflare, con las operaciones a su alrededor: coste por llamada " +
      "y presupuestos (FinOps), una batería de evaluación que valida cada despliegue y un juez LLM (scoring), contrato y " +
      "guardrails de contenido, CI/CD y controles de seguridad. Todo lo que ves se calcula en directo a partir del registro " +
      "de la propia plataforma.",
    askAgent: "Pregunta al agente", source: "Código fuente", architecture: "Arquitectura",
    readOnly: "Vista de solo lectura. El detalle de cada ejecución, la revisión humana y los interruptores de los agentes están en el admin, protegido con contraseña.",
    today: "Hoy (UTC)", last7: "Últimos 7 días",
    calls: "Llamadas al LLM", billed: "Facturado hoy (fuera del plan gratuito)", billedNote: "el uso de hoy cabe en la cuota gratuita",
    billedOver: "el uso ha superado la cuota gratuita",
    value: "Uso a precio de lista", valueNote: "neuronas × 0,011 $ / 1.000 — lo que costaría sin el plan gratuito",
    freeUsed: "Cuota gratuita usada por esta plataforma", neuronsOf: "de", neurons: "neuronas", errorRate: "Tasa de error",
    runs: "Ejecuciones", successRate: "Tasa de éxito", avgCost: "Valor medio por ejecución", judge: "Nota del juez",
    agreement: "Acuerdo juez / humano", labeled: "etiquetadas",
    valuePerDay: "Uso por día (precio de lista)", callsPerDay: "Llamadas por día", errorsPerDay: "Errores por día",
    judgePerDay: "Nota del juez por día", max: "máx.", noData: "Aún no hay datos.",
    guardrails: "Guardrails (7 días)", rule: "Regla", action: "Acción", count: "Veces", none: "Ninguno.",
    releases: "Releases", deployed: "Desplegado (UTC)", version: "Versión", commit: "Commit", noReleases: "No hay despliegues registrados.",
    agents: "Agentes", agent: "Agente", status: "Estado", guardrailMode: "Guardrails", budgetToday: "Presupuesto usado hoy",
    actions: "Acciones", purpose: "Propósito", of: "de",
    evalByRelease: "Evaluación en directo por release", evalNote: "la validación del despliegue: preguntas fijas, corrección determinista",
    evalRuns: "Ejecuciones", passRate: "Aciertos", avgPerTask: "Valor medio por tarea", noEval: "Aún no hay evaluaciones.",
    latestEval: "Últimas evaluaciones", when: "Cuándo (UTC)", task: "Tarea", question: "Pregunta", result: "Resultado",
    cost: "Valor", pass: "✓ acierto", fail: "✗ fallo",
    adminTitle: "LLMOps admin", publicView: "Vista pública de solo lectura",
    adminNote: "Los logs, tokens y coste por petición también están en el panel de Cloudflare → AI → AI Gateway →",
    adminNote2: "los errores y la latencia del Worker en Workers → Observability.",
    review: "Cola de revisión", reviewNote: "nota del juez por debajo de", reviewNote2: "sin etiquetar", nothingToReview: "Nada que revisar.",
    run: "Ejecución", label: "Etiqueta", recent: "Ejecuciones recientes", sourceCol: "Origen", steps: "Pasos", noRuns: "Aún no hay ejecuciones.",
  },
} as const;

export type TextKey = keyof (typeof TEXTS)["en"];

/** Short explanation shown on hover / tap of each card, chart and table. */
const TIPS = {
  en: {
    calls: "Model calls made today (UTC) by the copilot and the judge, from the platform's ledger.",
    billed: "What Cloudflare would charge today: only Workers AI neurons beyond the 10,000 free per day, plus any paid provider. $0 means everything fit in the free tier.",
    value: "What today's usage would cost at list price if there were no free tier. Budgets are measured in this figure.",
    freeUsed: "Neurons this platform used today out of the 10,000 free per day. The allocation is per Cloudflare account and shared with its other Workers (e.g. rag_system), whose use is not counted here.",
    errorRate: "Share of today's model calls that failed (provider errors, timeouts, quota exhausted).",
    runs: "Questions the agent handled in the last 7 days, including the post-deploy evaluation.",
    successRate: "Runs that ended with an answer. Runs stopped by a guardrail or a budget count as not successful.",
    avgCost: "Average list-price usage of one run (all its model calls).",
    judge: "Average grade the LLM judge gave to sampled runs: on purpose, grounded and helpful, scaled 0-1. Green from 0.7.",
    agreement: "How often the judge's verdict matches the 👍 / 👎 a human gave in the admin. Shows how far the judge can be trusted.",
    valuePerDay: "List-price usage per day over the last 14 days.",
    callsPerDay: "Model calls per day over the last 14 days.",
    errorsPerDay: "Failed model calls per day over the last 14 days.",
    judgePerDay: "Average judge score per day over the last 14 days.",
    guardrails: "Rules that fired in the last 7 days. Blocked: the run was stopped. Flagged: recorded only (monitor mode).",
    releases: "Last deployments of the Worker, written by the deploy workflow.",
    agents: "Registered agents, their switches (status, guardrail mode) and how much of their daily budget they used today.",
    evalByRelease: "After every deploy, 8 fixed questions with checkable answers run against the live Worker. The deploy fails below 75% or above the cost limit.",
    latestEval: "The most recent evaluation questions, graded automatically, with the judge's score next to each.",
  },
  es: {
    calls: "Llamadas al modelo hechas hoy (UTC) por el copiloto y el juez, según el registro de la plataforma.",
    billed: "Lo que Cloudflare cobraría hoy: solo las neuronas de Workers AI por encima de las 10.000 gratuitas al día, más cualquier proveedor de pago. 0 $ significa que todo cupo en el plan gratuito.",
    value: "Lo que costaría el uso de hoy a precio de lista si no existiera el plan gratuito. Los presupuestos se miden con esta cifra.",
    freeUsed: "Neuronas que esta plataforma ha usado hoy de las 10.000 gratuitas al día. La cuota es por cuenta de Cloudflare y se comparte con sus otros Workers (p. ej. rag_system), cuyo uso no se cuenta aquí.",
    errorRate: "Parte de las llamadas al modelo de hoy que fallaron (errores del proveedor, timeouts, cuota agotada).",
    runs: "Preguntas que el agente ha atendido en los últimos 7 días, incluida la evaluación tras cada despliegue.",
    successRate: "Ejecuciones que terminaron con una respuesta. Las que cortó un guardrail o un presupuesto cuentan como no exitosas.",
    avgCost: "Uso medio a precio de lista de una ejecución (todas sus llamadas al modelo).",
    judge: "Nota media que el juez LLM dio a las ejecuciones muestreadas: ceñirse al propósito, estar respaldada y ser útil, de 0 a 1. En verde desde 0,7.",
    agreement: "Con qué frecuencia el veredicto del juez coincide con el 👍 / 👎 que dio una persona en el admin. Indica cuánto fiarse del juez.",
    valuePerDay: "Uso a precio de lista por día en los últimos 14 días.",
    callsPerDay: "Llamadas al modelo por día en los últimos 14 días.",
    errorsPerDay: "Llamadas al modelo fallidas por día en los últimos 14 días.",
    judgePerDay: "Nota media del juez por día en los últimos 14 días.",
    guardrails: "Reglas que han saltado en los últimos 7 días. Blocked: se cortó la ejecución. Flagged: solo se registró (modo monitor).",
    releases: "Últimos despliegues del Worker, registrados por el workflow de deploy.",
    agents: "Agentes registrados, sus interruptores (estado, modo de guardrails) y cuánto de su presupuesto diario han usado hoy.",
    evalByRelease: "Tras cada despliegue, 8 preguntas fijas con respuesta comprobable se lanzan contra el Worker en directo. El despliegue falla por debajo del 75 % o por encima del límite de coste.",
    latestEval: "Las preguntas de evaluación más recientes, corregidas automáticamente, con la nota del juez al lado.",
  },
} as const;

export type TipKey = keyof (typeof TIPS)["en"];

/** An "i" that shows the explanation on hover or keyboard / tap focus (CSS only). */
export const tipper = (lang: Lang) => (key: TipKey): string =>
  `<span class="tip" tabindex="0" role="img" aria-label="${TIPS[lang][key]}" data-tip="${TIPS[lang][key]}">ⓘ</span>`;

export const translator = (lang: Lang) => (key: TextKey): string => TEXTS[lang][key];

/** EN · ES links that keep the current path. */
export function langSwitch(path: string, lang: Lang): string {
  const link = (l: Lang) => (l === lang ? `<strong>${l.toUpperCase()}</strong>` : `<a href="${path}?lang=${l}">${l.toUpperCase()}</a>`);
  return `<span class="small">${link("en")} · ${link("es")}</span>`;
}
