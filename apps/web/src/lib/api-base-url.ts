export function resolveApiBaseUrl(
  configured = process.env.NEXT_PUBLIC_API_URL,
  environment = process.env.NODE_ENV,
): string {
  const value = configured?.trim();
  if (!value) {
    if (environment === "production") {
      throw new Error("NEXT_PUBLIC_API_URL is required for production builds");
    }
    return "http://localhost:8000";
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("NEXT_PUBLIC_API_URL must be a valid HTTP(S) origin");
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    (url.pathname !== "/" && url.pathname !== "") ||
    url.search ||
    url.hash
  ) {
    throw new Error("NEXT_PUBLIC_API_URL must be a credential-free HTTP(S) origin");
  }
  return url.origin;
}

export const apiBaseUrl = resolveApiBaseUrl();
