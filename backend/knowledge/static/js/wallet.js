window.KnowledgeWallet = (() => {
  const YEYING_PROVIDER_NAMES = ["yeeying", "yeying", "__YEYING_PROVIDER__"];
  const YEYING_RDNS = "io.github.yeying";
  const DEFAULT_DISCOVERY_TIMEOUT_MS = 1200;
  const DEFAULT_WALLET_REQUEST_TIMEOUT_MS = 15000;
  let cachedProvider = null;

  function isProvider(candidate) {
    return Boolean(candidate && typeof candidate.request === "function");
  }

  function cacheProvider(provider) {
    if (isProvider(provider)) {
      cachedProvider = provider;
    }
    return provider;
  }

  function clearCachedProvider() {
    cachedProvider = null;
  }

  function getWindowEthereum() {
    if (typeof window === "undefined") return null;
    return window.ethereum || null;
  }

  function unwrapMaybeJsonMessage(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    if (!(text.startsWith("{") || text.startsWith("["))) return text;
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed?.detail === "string") return parsed.detail;
      if (typeof parsed?.message === "string") return parsed.message;
      return text;
    } catch {
      return text;
    }
  }

  function providerLooksLikeYeYing(provider, info) {
    if (!isProvider(provider)) return false;
    if (provider.isYeYing || provider.isYeying || provider.isYeYingMask || provider.isYeyingMask) return true;
    if (window.yeeying === provider || window.yeying === provider || window.__YEYING_PROVIDER__ === provider) return true;
    const rdns = String(info?.rdns || "").toLowerCase();
    if (rdns === YEYING_RDNS) return true;
    const providerName = String(provider.name || provider.providerName || provider.wallet || "").toLowerCase();
    return providerName.includes("yeying") || providerName.includes("yeeying");
  }

  function providerLooksLikeMetaMask(provider) {
    return Boolean(isProvider(provider) && provider.isMetaMask);
  }

  function findNamedYeYingProvider() {
    for (const name of YEYING_PROVIDER_NAMES) {
      const provider = window[name];
      if (isProvider(provider)) {
        return provider;
      }
    }
    return null;
  }

  function getWalletProvider(options = {}) {
    const preferYeYing = options.preferYeYing !== false;
    if (isProvider(cachedProvider)) {
      if (!preferYeYing || providerLooksLikeYeYing(cachedProvider)) {
        return cachedProvider;
      }
    }

    const namedYeYing = findNamedYeYingProvider();
    if (namedYeYing) {
      return cacheProvider(namedYeYing);
    }

    const windowEthereum = getWindowEthereum();
    if (preferYeYing && providerLooksLikeYeYing(windowEthereum)) {
      return cacheProvider(windowEthereum);
    }

    const providers = windowEthereum?.providers;
    if (Array.isArray(providers)) {
      if (preferYeYing) {
        const preferredYeYing = providers.find((provider) => providerLooksLikeYeYing(provider));
        if (preferredYeYing) return cacheProvider(preferredYeYing);
      }
      const preferredMetaMask = providers.find((provider) => providerLooksLikeMetaMask(provider));
      if (preferredMetaMask) return cacheProvider(preferredMetaMask);
      const fallback = providers.find((provider) => isProvider(provider));
      if (fallback) return cacheProvider(fallback);
    }

    if (isProvider(windowEthereum)) {
      return cacheProvider(windowEthereum);
    }

    return null;
  }

  function selectBestProvider(candidates, preferYeYing) {
    if (!Array.isArray(candidates) || candidates.length === 0) return null;
    if (preferYeYing) {
      const yeying = candidates.find((candidate) => providerLooksLikeYeYing(candidate.provider, candidate.info));
      if (yeying) return yeying.provider;
    }
    const metamask = candidates.find((candidate) => providerLooksLikeMetaMask(candidate.provider));
    if (metamask) return metamask.provider;
    return candidates[0].provider;
  }

  async function discoverProvider(options = {}) {
    const preferYeYing = options.preferYeYing !== false;
    const timeoutMs = Number(options.timeoutMs || DEFAULT_DISCOVERY_TIMEOUT_MS);
    const immediate = getWalletProvider({ preferYeYing });
    if (immediate && (!preferYeYing || providerLooksLikeYeYing(immediate))) {
      return immediate;
    }
    if (typeof window === "undefined") {
      return immediate;
    }

    return await new Promise((resolve) => {
      const discovered = [];
      let resolved = false;
      const cleanup = () => {
        window.removeEventListener("eip6963:announceProvider", onAnnounce);
        window.removeEventListener("ethereum#initialized", onEthereumInitialized);
        clearTimeout(timeoutId);
      };
      const safeResolve = (provider) => {
        if (resolved) return;
        resolved = true;
        cleanup();
        resolve(cacheProvider(provider) || null);
      };
      const onAnnounce = (event) => {
        const detail = event?.detail;
        if (!detail?.provider) return;
        discovered.push(detail);
        if (preferYeYing && providerLooksLikeYeYing(detail.provider, detail.info)) {
          safeResolve(detail.provider);
        }
      };
      const onEthereumInitialized = () => {
        const injected = getWalletProvider({ preferYeYing });
        if (!injected) return;
        if (preferYeYing && providerLooksLikeYeYing(injected)) {
          safeResolve(injected);
          return;
        }
        if (!preferYeYing) {
          safeResolve(injected);
        }
      };

      window.addEventListener("eip6963:announceProvider", onAnnounce);
      window.addEventListener("ethereum#initialized", onEthereumInitialized, { once: true });

      const timeoutId = setTimeout(() => {
        const best = selectBestProvider(discovered, preferYeYing) || getWalletProvider({ preferYeYing: false }) || getWindowEthereum();
        safeResolve(best || null);
      }, timeoutMs > 0 ? timeoutMs : DEFAULT_DISCOVERY_TIMEOUT_MS);

      try {
        window.dispatchEvent(new Event("eip6963:requestProvider"));
      } catch {
        // Ignore browsers/environments that do not support EIP-6963 events.
      }

      if (!preferYeYing && immediate) {
        safeResolve(immediate);
      }
    });
  }

  function hasWallet() {
    return Boolean(getWalletProvider({ preferYeYing: false }));
  }

  function getWalletName(provider = getWalletProvider()) {
    if (!provider) return "未检测到钱包";
    if (providerLooksLikeYeYing(provider)) return "夜莺钱包";
    if (providerLooksLikeMetaMask(provider)) return "MetaMask";
    return "Web3 钱包";
  }

  function getWalletErrorMessage(error) {
    if (!error) return "";
    if (typeof error === "string") return unwrapMaybeJsonMessage(error);
    if (error instanceof Error) return unwrapMaybeJsonMessage(error.message || String(error));
    if (typeof error.message === "string") return unwrapMaybeJsonMessage(error.message);
    if (typeof error.reason === "string") return unwrapMaybeJsonMessage(error.reason);
    if (typeof error.details === "string") return unwrapMaybeJsonMessage(error.details);
    if (typeof error.data?.message === "string") return unwrapMaybeJsonMessage(error.data.message);
    if (typeof error.cause?.message === "string") return unwrapMaybeJsonMessage(error.cause.message);
    return String(error);
  }

  function getWalletErrorCode(error) {
    const code = Number(error?.code);
    if (!Number.isNaN(code)) return code;
    const causeCode = Number(error?.cause?.code);
    if (!Number.isNaN(causeCode)) return causeCode;
    return null;
  }

  function isUserRejectedWalletAction(error) {
    if (getWalletErrorCode(error) === 4001) return true;
    const message = getWalletErrorMessage(error).toLowerCase();
    return (
      message.includes("user rejected") ||
      message.includes("user denied") ||
      message.includes("rejected the signature") ||
      message.includes("denied message signature") ||
      message.includes("denied transaction signature") ||
      message.includes("request rejected") ||
      message.includes("cancelled") ||
      message.includes("canceled")
    );
  }

  function formatWalletLoginError(error, fallback = "钱包登录失败，请稍后重试。") {
    if (isUserRejectedWalletAction(error)) {
      return "你已取消钱包签名，请在钱包弹窗中确认后再试。";
    }
    const message = getWalletErrorMessage(error).replace(/^ProviderRpcError:\s*/i, "").trim();
    if (!message) return fallback;
    return `钱包登录失败：${message}`;
  }

  function formatWalletActionError(error, fallback = "钱包操作失败，请稍后重试。") {
    if (isUserRejectedWalletAction(error)) {
      return "你已取消钱包签名，请在钱包弹窗中确认后再试。";
    }
    const message = getWalletErrorMessage(error).replace(/^ProviderRpcError:\s*/i, "").trim();
    if (!message) return fallback;
    return message;
  }

  function shouldRetryPersonalSign(error) {
    if (isUserRejectedWalletAction(error)) return false;
    const code = getWalletErrorCode(error);
    if (code === -32602) return true;
    const message = getWalletErrorMessage(error).toLowerCase();
    return (
      message.includes("invalid params") ||
      message.includes("missing value for required argument") ||
      message.includes("expected array value") ||
      message.includes("invalid array") ||
      message.includes("personal_sign") ||
      message.includes("invalid argument")
    );
  }

  function withTimeout(task, timeoutMs, message) {
    const value = Number(timeoutMs || 0);
    if (!value || value <= 0) return task;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(message)), value);
      Promise.resolve(task).then(
        (result) => {
          clearTimeout(timer);
          resolve(result);
        },
        (error) => {
          clearTimeout(timer);
          reject(error);
        },
      );
    });
  }

  async function requestAccounts(provider = null, options = {}) {
    const resolvedProvider = isProvider(provider) ? provider : await discoverProvider(options);
    if (!isProvider(resolvedProvider)) {
      throw new Error("未检测到钱包，请安装 MetaMask 或夜莺钱包");
    }
    const result = await withTimeout(
      resolvedProvider.request({ method: "eth_requestAccounts" }),
      options.timeoutMs || DEFAULT_WALLET_REQUEST_TIMEOUT_MS,
      "连接钱包超时，请检查扩展弹窗或页面授权",
    );
    if (!Array.isArray(result) || result.length === 0) {
      throw new Error("未获取到账户");
    }
    return result.map((item) => String(item)).filter(Boolean);
  }

  async function signChallenge(provider, address, message, options = {}) {
    const resolvedProvider = isProvider(provider) ? provider : await discoverProvider(options);
    if (!isProvider(resolvedProvider)) {
      throw new Error("未检测到可用钱包 provider");
    }
    if (!address) {
      throw new Error("签名前缺少钱包地址");
    }
    if (!message) {
      throw new Error("签名前缺少 challenge message");
    }

    const attempts = [
      [message, address],
      [address, message],
    ];
    let lastError = null;

    for (const params of attempts) {
      try {
        const signature = await withTimeout(
          resolvedProvider.request({
            method: "personal_sign",
            params,
          }),
          options.timeoutMs || DEFAULT_WALLET_REQUEST_TIMEOUT_MS,
          "钱包签名超时，请检查扩展弹窗并确认签名请求",
        );
        if (typeof signature !== "string" || !signature.trim()) {
          throw new Error("钱包未返回签名结果");
        }
        return signature;
      } catch (error) {
        if (isUserRejectedWalletAction(error)) {
          throw error;
        }
        lastError = error;
        if (!shouldRetryPersonalSign(error)) {
          break;
        }
      }
    }

    throw lastError || new Error("钱包签名失败");
  }

  return {
    getWalletProvider,
    discoverProvider,
    hasWallet,
    getWalletName,
    requestAccounts,
    signChallenge,
    clearCachedProvider,
    getWalletErrorMessage,
    formatWalletLoginError,
    formatWalletActionError,
    isUserRejectedWalletAction,
  };
})();
