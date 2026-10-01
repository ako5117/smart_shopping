/**
 * Temporary Cloudflare hosting for the Smart Shopping backend (until the DigitalOcean account exists).
 *
 * Each service runs unchanged in its own container (the same Dockerfiles will run on DigitalOcean).
 * This Worker is the front door:
 *
 *   /shop/...       -> Scan & Go          (public: customers' phones)
 *   /pay/...        -> Payments Service   (/pay/payments/mpesa/callback/... is public, for Daraja)
 *   /inventory/...  -> Inventory Service  (staff login)
 *   /dashboard/...  -> Store dashboard    (staff login)
 *
 * The prefix is stripped before the request reaches the container. Containers reach each other
 * through http://inventory.internal and http://payments.internal, which the Worker routes straight
 * to those containers, so service-to-service calls never leave Cloudflare or need the staff password.
 */
import { Container, ContainerProxy } from "@cloudflare/containers";

export { ContainerProxy };

export interface Env {
	INVENTORY: DurableObjectNamespace<Inventory>;
	PAYMENTS: DurableObjectNamespace<Payments>;
	DASHBOARD: DurableObjectNamespace<Dashboard>;
	SCAN_AND_GO: DurableObjectNamespace<ScanAndGo>;

	STORE_ID: string;
	STORE_NAME: string;
	STAFF_USER: string;
	STAFF_PASSWORD: string; // secret
	PUBLIC_BASE_URL: string; // e.g. https://smart-shopping.<account>.workers.dev

	DARAJA_ENV: string;
	DARAJA_SHORTCODE: string;
	DARAJA_TRANSACTION_TYPE: string;
	DARAJA_PARTY_B: string;
	DARAJA_CONSUMER_KEY: string; // secret
	DARAJA_CONSUMER_SECRET: string; // secret
	DARAJA_PASSKEY: string; // secret
	CALLBACK_SECRET: string; // secret
}

const INVENTORY_HOST = "inventory.internal";
const PAYMENTS_HOST = "payments.internal";
// One instance of each service. SQLite lives on the container's disk, so all requests must reach the same one.
const INSTANCE = "main";

const toInventory = (req: Request, env: Env) => env.INVENTORY.getByName(INSTANCE).fetch(req);
const toPayments = (req: Request, env: Env) => env.PAYMENTS.getByName(INSTANCE).fetch(req);

// Containers report ready once /health answers.
const PING = "localhost/health";

export class Inventory extends Container<Env> {
	defaultPort = 8010;
	sleepAfter = "24h";
	pingEndpoint = PING;
	envVars: Record<string, string> = { UVICORN_ROOT_PATH: "/inventory" }; // so /inventory/docs finds its OpenAPI schema
}

export class Payments extends Container<Env> {
	defaultPort = 8000;
	sleepAfter = "24h";
	pingEndpoint = PING;

	constructor(ctx: DurableObjectState<{}>, env: Env) {
		super(ctx, env);
		this.envVars = {
			DARAJA_ENV: env.DARAJA_ENV ?? "sandbox",
			DARAJA_CONSUMER_KEY: env.DARAJA_CONSUMER_KEY ?? "",
			DARAJA_CONSUMER_SECRET: env.DARAJA_CONSUMER_SECRET ?? "",
			DARAJA_SHORTCODE: env.DARAJA_SHORTCODE ?? "174379",
			DARAJA_PASSKEY: env.DARAJA_PASSKEY ?? "",
			DARAJA_TRANSACTION_TYPE: env.DARAJA_TRANSACTION_TYPE ?? "CustomerPayBillOnline",
			DARAJA_PARTY_B: env.DARAJA_PARTY_B ?? "",
			PUBLIC_BASE_URL: `${(env.PUBLIC_BASE_URL ?? "").replace(/\/$/, "")}/pay`,
			CALLBACK_SECRET: env.CALLBACK_SECRET ?? "",
			INVENTORY_URL: `http://${INVENTORY_HOST}`,
			UVICORN_ROOT_PATH: "/pay",
		};
	}
}

export class Dashboard extends Container<Env> {
	defaultPort = 8020;
	sleepAfter = "1h";
	pingEndpoint = PING;

	constructor(ctx: DurableObjectState<{}>, env: Env) {
		super(ctx, env);
		this.envVars = {
			STORE_ID: env.STORE_ID ?? "001",
			INVENTORY_URL: `http://${INVENTORY_HOST}`,
			UVICORN_ROOT_PATH: "/dashboard",
		};
	}
}

export class ScanAndGo extends Container<Env> {
	defaultPort = 8030;
	sleepAfter = "1h";
	pingEndpoint = PING;

	constructor(ctx: DurableObjectState<{}>, env: Env) {
		super(ctx, env);
		this.envVars = {
			STORE_ID: env.STORE_ID ?? "001",
			STORE_NAME: env.STORE_NAME ?? "Smart Shopping",
			INVENTORY_URL: `http://${INVENTORY_HOST}`,
			PAYMENTS_URL: `http://${PAYMENTS_HOST}`,
			UVICORN_ROOT_PATH: "/shop",
		};
	}
}

// Assigned (not declared as `static outboundByHost = ...` class fields) so the base class's static
// setter runs and registers the handler; a class field would bypass it and the call would hit DNS.
Payments.outboundByHost = { [INVENTORY_HOST]: toInventory };
Dashboard.outboundByHost = { [INVENTORY_HOST]: toInventory };
ScanAndGo.outboundByHost = { [INVENTORY_HOST]: toInventory, [PAYMENTS_HOST]: toPayments };

type Route = {
	prefix: string;
	target: (env: Env) => { fetch(req: Request): Promise<Response> };
	isPublic?: (path: string) => boolean;
};

const ROUTES: Route[] = [
	{ prefix: "/shop", target: (env) => env.SCAN_AND_GO.getByName(INSTANCE), isPublic: () => true },
	{
		prefix: "/pay",
		target: (env) => env.PAYMENTS.getByName(INSTANCE),
		isPublic: (path) => path.startsWith("/payments/mpesa/callback/"),
	},
	{ prefix: "/inventory", target: (env) => env.INVENTORY.getByName(INSTANCE) },
	{ prefix: "/dashboard", target: (env) => env.DASHBOARD.getByName(INSTANCE) },
];

/** Constant-time string comparison, so the password can't be guessed one character at a time. */
function safeEqual(a: string, b: string): boolean {
	const enc = new TextEncoder();
	const x = enc.encode(a), y = enc.encode(b);
	let diff = x.length ^ y.length;
	for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
	return diff === 0;
}

function isStaff(req: Request, env: Env): boolean {
	const header = req.headers.get("Authorization") ?? "";
	if (!header.startsWith("Basic ")) return false;
	let decoded: string;
	try {
		decoded = atob(header.slice(6));
	} catch {
		return false;
	}
	const sep = decoded.indexOf(":");
	if (sep < 0) return false;
	return safeEqual(decoded.slice(0, sep), env.STAFF_USER || "staff") && safeEqual(decoded.slice(sep + 1), env.STAFF_PASSWORD);
}

const INDEX = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smart Shopping</title>
<body style="font:16px system-ui;max-width:32rem;margin:3rem auto;padding:0 16px">
<h1>Smart Shopping</h1><p>Temporary hosting for the Phase 1 services.</p>
<ul><li><a href="/shop/">Scan &amp; Go</a> (customers)</li>
<li><a href="/dashboard/">Store dashboard</a> (staff login)</li>
<li><a href="/inventory/docs">Inventory Service API</a> (staff login)</li>
<li><a href="/pay/docs">Payments Service API</a> (staff login)</li></ul></body>`;

export default {
	async fetch(req: Request, env: Env): Promise<Response> {
		const url = new URL(req.url);
		if (url.pathname === "/") return new Response(INDEX, { headers: { "content-type": "text/html; charset=utf-8" } });
		if (url.pathname === "/health") return Response.json({ status: "ok" });

		const route = ROUTES.find((r) => url.pathname === r.prefix || url.pathname.startsWith(r.prefix + "/"));
		if (!route) return new Response("Not found", { status: 404 });
		if (url.pathname === route.prefix) return Response.redirect(`${url.origin}${route.prefix}/${url.search}`, 301);

		const path = url.pathname.slice(route.prefix.length);
		if (!route.isPublic?.(path)) {
			if (!env.STAFF_PASSWORD) return new Response("STAFF_PASSWORD is not set on this Worker", { status: 503 });
			if (!isStaff(req, env)) {
				return new Response("Staff login required", {
					status: 401,
					headers: { "WWW-Authenticate": 'Basic realm="Smart Shopping staff", charset="UTF-8"' },
				});
			}
		}

		const inner = new URL(path + url.search, url.origin);
		const headers = new Headers(req.headers);
		headers.delete("Authorization"); // the services don't use it; keep the password out of their logs
		const forwarded = new Request(inner, { method: req.method, headers, body: req.body, redirect: "manual" });
		return route.target(env).fetch(forwarded);
	},
} satisfies ExportedHandler<Env>;
