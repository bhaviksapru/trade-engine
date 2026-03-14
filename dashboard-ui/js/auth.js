// auth.js - Cognito + Google OAuth PKCE flow
// Handles login, token storage, token refresh, logout

const Auth = (() => {
  const TOKEN_KEY  = "trade_engine_id_token";
  const EXPIRY_KEY = "trade_engine_token_expiry";

  function getToken() {
    const token  = localStorage.getItem(TOKEN_KEY);
    const expiry = parseInt(localStorage.getItem(EXPIRY_KEY) || "0", 10);
    if (!token || Date.now() > expiry) return null;
    return token;
  }

  function saveToken(idToken, expiresIn = 3600) {
    localStorage.setItem(TOKEN_KEY, idToken);
    localStorage.setItem(EXPIRY_KEY, Date.now() + expiresIn * 1000 - 60000); // 1min buffer
  }

  function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EXPIRY_KEY);
  }

  function loginRedirect() {
    const { cognitoDomain, cognitoClientId, dashboardUrl, cognitoScopes } = window.CONFIG;
    const params = new URLSearchParams({
      client_id:     cognitoClientId,
      response_type: "code",
      scope:         cognitoScopes,
      redirect_uri:  dashboardUrl,
    });
    window.location.href = `${cognitoDomain}/oauth2/authorize?${params}`;
  }

  async function handleCallback() {
    const params = new URLSearchParams(window.location.search);
    const code   = params.get("code");
    if (!code) return false;

    // Exchange code for tokens
    const { cognitoDomain, cognitoClientId, dashboardUrl } = window.CONFIG;
    const body = new URLSearchParams({
      grant_type:   "authorization_code",
      client_id:    cognitoClientId,
      code:         code,
      redirect_uri: dashboardUrl,
    });

    try {
      const resp = await fetch(`${cognitoDomain}/oauth2/token`, {
        method:  "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body:    body.toString(),
      });

      if (!resp.ok) throw new Error(`Token exchange failed: ${resp.status}`);

      const data = await resp.json();
      saveToken(data.id_token, data.expires_in);

      // Clean code from URL without reload
      window.history.replaceState({}, document.title, window.location.pathname);
      return true;
    } catch (e) {
      console.error("Token exchange error:", e);
      return false;
    }
  }

  function logout() {
    clearToken();
    const { cognitoDomain, cognitoClientId, dashboardUrl } = window.CONFIG;
    const params = new URLSearchParams({ client_id: cognitoClientId, logout_uri: dashboardUrl });
    window.location.href = `${cognitoDomain}/logout?${params}`;
  }

  // Call on every page - redirects to login if not authenticated
  async function requireAuth() {
    // Check for OAuth callback code first
    if (window.location.search.includes("code=")) {
      const ok = await handleCallback();
      if (!ok) { loginRedirect(); return null; }
    }

    const token = getToken();
    if (!token) { loginRedirect(); return null; }
    return token;
  }

  return { requireAuth, getToken, logout };
})();
