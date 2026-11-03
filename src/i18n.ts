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
    calls: "LLM calls", billed: "Billed today", billedNote: "within the free allocation", billedOver: "beyond the free allocation",
    value: "Usage at list price", valueNote: "neurons × $0.011 / 1,000 — what it would cost without the free tier",
    freeUsed: "Free Workers AI allocation used", neuronsOf: "of", neurons: "neurons", errorRate: "Error rate",
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
    calls: "Llamadas al LLM", billed: "Facturado hoy", billedNote: "dentro de la cuota gratuita", billedOver: "por encima de la cuota gratuita",
    value: "Uso a precio de lista", valueNote: "neuronas × 0,011 $ / 1.000 — lo que costaría sin el plan gratuito",
    freeUsed: "Cuota gratuita de Workers AI usada", neuronsOf: "de", neurons: "neuronas", errorRate: "Tasa de error",
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

export const translator = (lang: Lang) => (key: TextKey): string => TEXTS[lang][key];

/** EN · ES links that keep the current path. */
export function langSwitch(path: string, lang: Lang): string {
  const link = (l: Lang) => (l === lang ? `<strong>${l.toUpperCase()}</strong>` : `<a href="${path}?lang=${l}">${l.toUpperCase()}</a>`);
  return `<span class="small">${link("en")} · ${link("es")}</span>`;
}
