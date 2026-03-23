window.KnowledgeWarehouseBridge = (() => {
  class WarehouseBridgeError extends Error {
    constructor(message, options = {}) {
      super(message);
      this.name = "WarehouseBridgeError";
      this.status = options.status || 0;
      this.url = options.url || "";
      this.payload = options.payload;
    }
  }

  function normalizeBaseUrl(baseUrl) {
    return String(baseUrl || "").trim().replace(/\/+$/, "");
  }

  function normalizeDavPrefix(prefix) {
    const value = "/" + String(prefix || "/dav").trim().replace(/^\/+/, "");
    return value === "/" ? "/" : value.replace(/\/+$/, "");
  }

  function normalizeWarehousePath(path) {
    const raw = String(path || "/").trim();
    const normalized = "/" + raw.replace(/^\/+/, "").replace(/\/+$/, "");
    return normalized === "/" ? "/" : normalized;
  }

  function encodeWarehousePath(path) {
    return normalizeWarehousePath(path)
      .split("/")
      .map((segment, index) => (index === 0 ? "" : encodeURIComponent(segment)))
      .join("/");
  }

  function joinUrl(baseUrl, path) {
    return `${normalizeBaseUrl(baseUrl)}/${String(path || "").replace(/^\/+/, "")}`;
  }

  function buildDavUrl(baseUrl, webdavPrefix, path) {
    return `${normalizeBaseUrl(baseUrl)}${normalizeDavPrefix(webdavPrefix)}${encodeWarehousePath(path)}`;
  }

  function extractSdkData(payload) {
    if (payload && typeof payload === "object" && payload.data && typeof payload.data === "object") {
      return payload.data;
    }
    return payload;
  }

  function extractTextPayload(payload) {
    if (typeof payload === "string") return payload.trim();
    const data = extractSdkData(payload);
    if (typeof data === "string") return data.trim();
    if (typeof data?.message === "string") return data.message.trim();
    if (typeof payload?.message === "string") return payload.message.trim();
    if (typeof payload?.detail === "string") return payload.detail.trim();
    return "";
  }

  async function readResponsePayload(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      try {
        return await response.json();
      } catch {
        return null;
      }
    }
    try {
      return await response.text();
    } catch {
      return null;
    }
  }

  function formatWarehouseBridgeError(error, fallback = "warehouse 临时授权失败，请稍后重试。") {
    if (!error) return fallback;
    if (error instanceof WarehouseBridgeError) {
      if (error.message) return error.message;
      return fallback;
    }
    if (error instanceof TypeError) {
      return "浏览器无法直接访问 warehouse API，请检查网络或 CORS 配置。";
    }
    if (error instanceof Error && error.message) {
      return error.message;
    }
    if (typeof error === "string" && error.trim()) {
      return error.trim();
    }
    return fallback;
  }

  async function requestJson(baseUrl, path, options = {}) {
    const url = joinUrl(baseUrl, path);
    const headers = {
      accept: "application/json",
      ...(options.headers || {}),
    };
    if (options.token) {
      headers.Authorization = `Bearer ${options.token}`;
    }
    if (options.body !== undefined && !(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }

    let response;
    try {
      response = await fetch(url, {
        method: options.method || "GET",
        headers,
        credentials: options.credentials || "omit",
        body:
          options.body === undefined
            ? undefined
            : options.body instanceof FormData
              ? options.body
              : JSON.stringify(options.body),
      });
    } catch (error) {
      throw new WarehouseBridgeError(formatWarehouseBridgeError(error), { url });
    }

    const payload = await readResponsePayload(response);
    if (!response.ok) {
      const text = extractTextPayload(payload) || `HTTP ${response.status}`;
      throw new WarehouseBridgeError(text, {
        status: response.status,
        url,
        payload,
      });
    }
    return payload;
  }

  function extractWarehouseChallenge(payload) {
    const data = extractSdkData(payload);
    if (typeof data?.challenge === "string") return data.challenge;
    if (typeof payload?.challenge === "string") return payload.challenge;
    return "";
  }

  function extractWarehouseToken(payload) {
    const data = extractSdkData(payload);
    if (typeof data?.token === "string") return data.token;
    if (typeof payload?.token === "string") return payload.token;
    return "";
  }

  function buildDirectoryChain(targetPath) {
    const normalized = normalizeWarehousePath(targetPath);
    const segments = normalized.split("/").filter(Boolean);
    let current = "";
    const chain = [];
    for (const segment of segments) {
      current += `/${segment}`;
      if (current === "/apps") continue;
      chain.push(current);
    }
    return chain;
  }

  async function loginWithWallet({ baseUrl, provider, walletHelper }) {
    if (!walletHelper) {
      throw new WarehouseBridgeError("knowledge 钱包适配层未加载");
    }
    const resolvedProvider = provider || (await walletHelper.discoverProvider?.({ timeoutMs: 1200 })) || walletHelper.getWalletProvider?.();
    if (!resolvedProvider) {
      throw new WarehouseBridgeError("未检测到钱包，请先安装或解锁夜莺钱包。");
    }
    const accounts = await walletHelper.requestAccounts(resolvedProvider, { timeoutMs: 15000 });
    const address = String(accounts?.[0] || "").trim();
    if (!address) {
      throw new WarehouseBridgeError("未获取到可用钱包账户。");
    }
    const challengePayload = await requestJson(baseUrl, "/api/v1/public/auth/challenge", {
      method: "POST",
      body: { address },
    });
    const challenge = extractWarehouseChallenge(challengePayload);
    if (!challenge) {
      throw new WarehouseBridgeError("warehouse challenge 返回缺少 challenge。");
    }
    const signature = await walletHelper.signChallenge(resolvedProvider, address, challenge, { timeoutMs: 20000 });
    const verifyPayload = await requestJson(baseUrl, "/api/v1/public/auth/verify", {
      method: "POST",
      body: { address, signature },
    });
    const token = extractWarehouseToken(verifyPayload);
    if (!token) {
      throw new WarehouseBridgeError("warehouse verify 返回缺少 token。");
    }
    return {
      address,
      token,
      providerName: walletHelper.getWalletName?.(resolvedProvider) || "Web3 钱包",
    };
  }

  async function createAccessKey({ baseUrl, token, name, permissions, expiresValue = 0, expiresUnit = "day" }) {
    const payload = await requestJson(baseUrl, "/api/v1/public/webdav/access-keys/create", {
      method: "POST",
      token,
      body: {
        name,
        permissions,
        expiresValue,
        expiresUnit,
      },
    });
    return payload;
  }

  async function bindAccessKey({ baseUrl, token, id, path }) {
    return await requestJson(baseUrl, "/api/v1/public/webdav/access-keys/bind", {
      method: "POST",
      token,
      body: {
        id,
        path: normalizeWarehousePath(path),
      },
    });
  }

  async function listAccessKeys({ baseUrl, token }) {
    const payload = await requestJson(baseUrl, "/api/v1/public/webdav/access-keys/list", {
      method: "GET",
      token,
    });
    return Array.isArray(payload?.items) ? payload.items : [];
  }

  async function mkcol(baseUrl, webdavPrefix, token, path) {
    const url = buildDavUrl(baseUrl, webdavPrefix, path);
    let response;
    try {
      response = await fetch(url, {
        method: "MKCOL",
        headers: {
          Authorization: `Bearer ${token}`,
        },
        credentials: "omit",
      });
    } catch (error) {
      throw new WarehouseBridgeError(formatWarehouseBridgeError(error), { url });
    }
    if (response.ok || response.status === 405) {
      return { ok: true, existed: response.status === 405 };
    }
    const payload = await readResponsePayload(response);
    throw new WarehouseBridgeError(extractTextPayload(payload) || `创建目录失败: ${response.status}`, {
      status: response.status,
      url,
      payload,
    });
  }

  async function ensureDirectoryChain({ baseUrl, webdavPrefix, token, targetPath }) {
    const chain = buildDirectoryChain(targetPath);
    for (const path of chain) {
      await mkcol(baseUrl, webdavPrefix, token, path);
    }
    return chain;
  }

  return {
    normalizeBaseUrl,
    normalizeDavPrefix,
    normalizeWarehousePath,
    buildDirectoryChain,
    formatWarehouseBridgeError,
    loginWithWallet,
    createAccessKey,
    bindAccessKey,
    listAccessKeys,
    ensureDirectoryChain,
  };
})();
