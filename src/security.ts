/**
 * security.ts — HTTP hardening shared by every Worker response.
 *
 *   securityHeaders  CSP without scripts for server pages, no framing, no sniffing
 *   adminAuth        HTTP Basic auth against the ADMIN_TOKEN secret (any user
 *                    name), compared in constant time; admin is off when the
 *                    secret is not set
 *   sameOrigin       rejects cross-site POSTs to /admin (CSRF: the browser
 *                    would otherwise attach the Basic credentials)
 */

import type { Env } from "./env";

const HEADERS: Record<string, string> = {
  "Content-Security-Policy":
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "Cache-Control": "no-store",
};

export function securityHeaders(resp: Response): Response {
  const out = new Response(resp.body, resp);
  for (const [k, v] of Object.entries(HEADERS)) if (!out.headers.has(k)) out.headers.set(k, v);
  return out;
}

/** Constant-time string comparison (length is not secret here). */
export function safeEqual(a: string, b: string): boolean {
  const x = new TextEncoder().encode(a);
  const y = new TextEncoder().encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

/** null when authorized, otherwise the response to send. */
export function adminAuth(req: Request, env: Env): Response | null {
  if (!env.ADMIN_TOKEN) {
    return new Response("Admin is disabled: set the ADMIN_TOKEN secret (wrangler secret put ADMIN_TOKEN).", { status: 503 });
  }
  const header = req.headers.get("Authorization") ?? "";
  if (header.startsWith("Basic ")) {
    let decoded = "";
    try {
      decoded = atob(header.slice(6));
    } catch {
      /* invalid base64: falls through to 401 */
    }
    const password = decoded.slice(decoded.indexOf(":") + 1);
    if (decoded.includes(":") && safeEqual(password, env.ADMIN_TOKEN)) return null;
  }
  return new Response("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="llmops admin", charset="UTF-8"' },
  });
}

export function sameOrigin(req: Request): boolean {
  const origin = req.headers.get("Origin");
  return origin !== null && origin === new URL(req.url).origin;
}
