import { API_BASE } from "./config";
import type {
  ApiOrder,
  ApiStockRow,
  PickPathResponse,
  TopSellersResponse,
} from "./types";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`GET ${path} -> ${response.status}`);
  }
  return (await response.json()) as T;
}

interface Paginated<T> {
  results: T[];
}

export async function fetchRecentOrders(limit = 30): Promise<ApiOrder[]> {
  const page = await getJson<Paginated<ApiOrder>>(`/api/orders/?limit=${limit}`);
  return page.results;
}

export async function fetchStock(): Promise<ApiStockRow[]> {
  const page = await getJson<Paginated<ApiStockRow>>("/api/stock/?limit=200");
  return page.results;
}

export function fetchTopSellers(): Promise<TopSellersResponse> {
  return getJson<TopSellersResponse>("/api/analytics/top-sellers/");
}

export function fetchPickPath(orderId: string, engine?: string): Promise<PickPathResponse> {
  const suffix = engine ? `?engine=${engine}` : "";
  return getJson<PickPathResponse>(`/api/orders/${orderId}/pick-path/${suffix}`);
}

export async function placeDemoOrder(): Promise<void> {
  const response = await fetch(`${API_BASE}/api/demo/orders/`, { method: "POST" });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? `demo order failed (${response.status})`);
  }
}
