// api.js — REST calls to FastAPI on Fargate
// All calls attach the Cognito JWT automatically

const API = (() => {
  function headers() {
    const token = Auth.getToken();
    return {
      "Authorization": `Bearer ${token}`,
      "Content-Type":  "application/json",
    };
  }

  async function get(path) {
    const resp = await fetch(`${window.CONFIG.apiUrl}${path}`, { headers: headers() });
    if (resp.status === 401) { Auth.logout(); return null; }
    if (!resp.ok) throw new Error(`API error ${resp.status}: ${path}`);
    return resp.json();
  }

  async function post(path, body = {}) {
    const resp = await fetch(`${window.CONFIG.apiUrl}${path}`, {
      method:  "POST",
      headers: headers(),
      body:    JSON.stringify(body),
    });
    if (resp.status === 401) { Auth.logout(); return null; }
    if (!resp.ok) throw new Error(`API error ${resp.status}: ${path}`);
    return resp.json();
  }

  return {
    getPositions:        ()          => get("/positions"),
    getTrades:           (days = 7)  => get(`/positions/trades?days=${days}`),
    getRisk:             ()          => get("/positions/risk"),
    getHealth:           ()          => get("/health"),

    closeAll:            ()          => post("/actions/close-all-positions"),
    closePosition:       (id)        => post(`/actions/close-position/${id}`),
    pauseTrading:        ()          => post("/actions/pause-trading"),
    resumeTrading:       ()          => post("/actions/resume-trading"),
    updateNotifications: (prefs)     => post("/actions/notifications/update", prefs),
    setRiskParameters:   (params)    => post("/actions/set-risk-parameters", params),
  };
})();
