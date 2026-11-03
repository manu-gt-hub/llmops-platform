/**
 * index.ts — Worker entry point.
 *
 *   GET  /              chat UI (public/index.html)
 *   POST /api/ask       ask the research agent {question} → answer, run id, cost
 *   GET  /health        liveness
 *   GET  /version       version, commit, active provider and model
 *   GET  /dashboard     public read-only dashboard (admin.ts)
 *   *    /admin...      admin dashboard (ADMIN_TOKEN), see admin.ts
 *
 * Public traffic is limited per IP (RATE_LIMITER) and by each agent's daily
 * budget; every response carries the security headers of security.ts.
 */

import { handleAdmin, publicDashboard } from "./admin";
import { AGENT, runAgent } from "./agent";
import type { Env } from "./env";
import { isSuiteQuestion } from "./evals";
import { pickLang } from "./i18n";
import { CHARTERS } from "./governance";
import { judgeRun } from "./judge";
import { activeModel } from "./llm";
import { adminAuth, sameOrigin, securityHeaders } from "./security";

// The entry module may only export handlers, so this stays private
const MAX_QUESTION_CHARS = 500;

const error = (status: number, code: string, message: string) => Response.json({ error: { code, message } }, { status });

async function ask(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const ip = req.headers.get("CF-Connecting-IP") ?? "unknown";
  if (env.RATE_LIMITER && !(await env.RATE_LIMITER.limit({ key: ip })).success) {
    return error(429, "rate_limited", "Too many questions, wait a minute.");
  }
  let body: { question?: unknown; task_id?: unknown; source?: unknown };
  try {
    body = await req.json();
  } catch {
    return error(400, "invalid_json", "Send a JSON body: {\"question\": \"...\"}");
  }
  const question = typeof body.question === "string" ? body.question.trim() : "";
  if (!question) return error(400, "missing_question", "The question is empty.");
  if (question.length > MAX_QUESTION_CHARS) return error(400, "question_too_long", `At most ${MAX_QUESTION_CHARS} characters.`);

  const isEval = body.source === "eval" && isSuiteQuestion(body.task_id, question);
  const result = await runAgent(env, question, {
    source: isEval ? "eval" : "chat",
    taskId: isEval ? (body.task_id as string) : null,
  });
  // Grade a sample of answered runs after responding (no latency for the user)
  if (result.answer && Math.random() < CHARTERS[AGENT].judge_sample_rate) {
    ctx.waitUntil(judgeRun(env, result.runId));
  }
  return Response.json({
    run_id: result.runId,
    status: result.status,
    stop_reason: result.stopReason,
    answer: result.answer,
    steps: result.steps,
    cost_usd: result.costUsd,
  });
}

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const { pathname } = new URL(req.url);
    let resp: Response;
    try {
      if (pathname === "/health") resp = Response.json({ status: "ok" });
      else if (pathname === "/version") {
        resp = Response.json({ version: env.APP_VERSION, commit: env.GIT_COMMIT, ...activeModel(env), ai_gateway: env.AI_GATEWAY_ID });
      } else if (pathname === "/api/ask") {
        resp = req.method === "POST" ? await ask(req, env, ctx) : error(405, "method_not_allowed", "Use POST.");
      } else if (pathname === "/dashboard") {
        resp = await publicDashboard(env, pickLang(req));
      } else if (pathname === "/admin" || pathname.startsWith("/admin/")) {
        resp = adminAuth(req, env) ??
          (req.method === "POST" && !sameOrigin(req)
            ? new Response("Cross-site request refused", { status: 403 })
            : await handleAdmin(req, env, pathname, pickLang(req)));
      } else {
        resp = await env.ASSETS.fetch(req);
      }
    } catch (e) {
      console.error(e); // details in Workers Observability, never to the client
      resp = error(500, "internal_error", "Something went wrong.");
    }
    return securityHeaders(resp);
  },
} satisfies ExportedHandler<Env>;
